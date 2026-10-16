"""Token-based duplication detection across Python files.

Each file is tokenized with the standard-library :mod:`tokenize` module and
reduced to a normalized token stream: comments, layout tokens, and the exact
spelling of numbers and strings are discarded so formatting differences do not
hide a clone. Windows of ``min_tokens`` tokens are grouped by content; a group
that occurs at ``min_occurrences`` or more non-overlapping positions is
extended to its maximal length, and nested groups are collapsed so each clone
is reported once. Results are deterministic and capped.
"""

from __future__ import annotations

import io
import tokenize
from collections.abc import Mapping, Sequence
from pathlib import Path

from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.types import (
    Category,
    FileKind,
    Finding,
    Location,
    Remediation,
    Severity,
    Status,
)

__all__ = ["DuplicationCheck"]

#: Tokens below this length are never reported as duplication.
_DEFAULT_MIN_TOKENS = 50
#: Occurrences of an identical window before it counts as duplicated.
_DEFAULT_MIN_OCCURRENCES = 2
#: Default upper bound on emitted duplication findings per run.
_MAX_FINDINGS = 50

#: Token types that carry no comparable source content.
_NON_SOURCE_TOKENS = frozenset(
    {
        tokenize.ENCODING,
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
    }
)


def _relative_posix(path: Path, root: Path) -> str:
    """Return ``path`` relative to ``root`` as a POSIX path.

    Falls back to the path itself when it cannot be expressed relative to
    ``root`` (for example in hand-built test contexts).
    """

    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _int_option(options: Mapping[str, object], key: str, default: int) -> int:
    """Read integer option ``key``; fall back to ``default`` when unusable."""

    value = options.get(key, default)
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def _result(check_id: str, findings: list[Finding], *, notes: str | None = None) -> CheckResult:
    """Wrap ``findings`` in a :class:`CheckResult` (WARN when anything fired)."""

    status = Status.WARN if findings else Status.PASS
    return CheckResult(check=check_id, findings=tuple(findings), status=status, notes=notes)


#: A normalized token paired with its starting source line.
_Token = tuple[str, int]
#: One occurrence of a clone, as ``(file, start index)``.
_Occurrence = tuple[str, int]


def _normalized(token: tokenize.TokenInfo) -> str:
    """Return the comparison key for ``token``.

    Numbers and strings collapse to placeholders so only their presence (not
    their literal value) participates in matching.
    """

    if token.type == tokenize.NUMBER:
        return "<num>"
    if token.type == tokenize.STRING:
        return "<str>"
    return token.string


def _tokenize_file(text: str) -> list[_Token] | None:
    """Tokenize ``text`` into normalized tokens, or ``None`` when malformed."""

    tokens: list[_Token] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(text).readline):
            if token.type in _NON_SOURCE_TOKENS:
                continue
            tokens.append((_normalized(token), token.start[0]))
    except (tokenize.TokenError, IndentationError):
        return None
    return tokens


def _extend_clone(
    files: Mapping[str, Sequence[_Token]], occurrences: Sequence[_Occurrence], length: int
) -> int:
    """Grow ``length`` while every occurrence keeps matching token by token."""

    while True:
        reference: str | None = None
        for file, start in occurrences:
            tokens = files[file]
            index = start + length
            if index >= len(tokens):
                return length
            value = tokens[index][0]
            if reference is None:
                reference = value
            elif value != reference:
                return length
        length += 1


def _merge_intervals(starts: Sequence[int], length: int) -> list[tuple[int, int]]:
    """Merge overlapping ``[start, start + length)`` intervals within one file."""

    intervals = sorted((start, start + length) for start in starts)
    merged: list[tuple[int, int]] = []
    for begin, end in intervals:
        if merged and begin < merged[-1][1]:
            if end > merged[-1][1]:
                merged[-1] = (merged[-1][0], end)
            continue
        merged.append((begin, end))
    return merged


def _covered(spans: Sequence[tuple[int, int]], begin: int, end: int) -> bool:
    """True when ``[begin, end)`` is fully inside one of ``spans``."""

    return any(span_begin <= begin and end <= span_end for span_begin, span_end in spans)


def _clone_signature(occurrences: Sequence[_Occurrence]) -> str:
    """Return a stable signature for a set of occurrences."""

    return ";".join(f"{file}:{start}" for file, start in sorted(occurrences))


#: A maximal clone: token length, its occurrences, and merged per-file spans.
_Candidate = tuple[int, list[_Occurrence], list[tuple[str, int, int]]]


def _duplication_options(ctx: CheckContext, check_id: str) -> tuple[int, int, int]:
    """Read and bound the ``(min_tokens, min_occurrences, max_findings)`` options."""

    options = ctx.rule_options(
        check_id,
        {
            "min_tokens": _DEFAULT_MIN_TOKENS,
            "min_occurrences": _DEFAULT_MIN_OCCURRENCES,
            "max_findings": _MAX_FINDINGS,
        },
    )
    min_tokens = max(1, _int_option(options, "min_tokens", _DEFAULT_MIN_TOKENS))
    min_occurrences = max(2, _int_option(options, "min_occurrences", _DEFAULT_MIN_OCCURRENCES))
    max_findings = max(1, _int_option(options, "max_findings", _MAX_FINDINGS))
    return min_tokens, min_occurrences, max_findings


def _collect_files(
    ctx: CheckContext, file_kinds: frozenset[FileKind], min_tokens: int
) -> dict[str, list[_Token]]:
    """Tokenize every Python file long enough to contain a candidate clone."""

    files: dict[str, list[_Token]] = {}
    for path in sorted(ctx.files_for(file_kinds)):
        tokens = _tokenize_file(ctx.read_text(path))
        if tokens is None or len(tokens) < min_tokens:
            continue
        files[_relative_posix(path, ctx.root)] = tokens
    return files


def _window_groups(
    files: Mapping[str, Sequence[_Token]], min_tokens: int
) -> dict[tuple[str, ...], list[_Occurrence]]:
    """Group every sliding window of ``min_tokens`` by its normalized content."""

    groups: dict[tuple[str, ...], list[_Occurrence]] = {}
    for file, tokens in files.items():
        keys = [token[0] for token in tokens]
        for start in range(len(keys) - min_tokens + 1):
            window = tuple(keys[start : start + min_tokens])
            groups.setdefault(window, []).append((file, start))
    return groups


def _merged_spans(
    occurrences: Sequence[_Occurrence],
    files: Mapping[str, Sequence[_Token]],
    length: int,
    min_occurrences: int,
) -> list[tuple[str, int, int]] | None:
    """Merge each file's occurrences into spans, or ``None`` when too few remain."""

    by_file: dict[str, list[int]] = {}
    for file, start in occurrences:
        by_file.setdefault(file, []).append(start)
    merged: list[tuple[str, int, int]] = []
    for file, starts in by_file.items():
        for begin, end in _merge_intervals(starts, length):
            merged.append((file, begin, end))
    return merged if len(merged) >= min_occurrences else None


def _candidate_clones(
    groups: Mapping[tuple[str, ...], list[_Occurrence]],
    files: Mapping[str, Sequence[_Token]],
    min_tokens: int,
    min_occurrences: int,
) -> list[_Candidate]:
    """Extend recurring windows to maximal clones with merged spans."""

    candidates: list[_Candidate] = []
    for occurrences in groups.values():
        if len(occurrences) < min_occurrences:
            continue
        length = _extend_clone(files, occurrences, min_tokens)
        merged = _merged_spans(occurrences, files, length, min_occurrences)
        if merged is None:
            continue
        candidates.append((length, list(occurrences), merged))
    return candidates


def _select_eligible(candidates: Sequence[_Candidate]) -> list[_Candidate]:
    """Drop candidates fully covered by a longer clone already accepted."""

    eligible: list[_Candidate] = []
    coverage: dict[str, list[tuple[int, int]]] = {}
    for candidate in candidates:
        _, _, merged = candidate
        if all(_covered(coverage.get(file, ()), begin, end) for file, begin, end in merged):
            continue
        eligible.append(candidate)
        for file, begin, end in merged:
            coverage.setdefault(file, []).append((begin, end))
    return eligible


def _occurrence_locations(
    merged: Sequence[tuple[str, int, int]],
    files: Mapping[str, Sequence[_Token]],
) -> list[str]:
    """Return ``file:line`` for every occurrence, deterministically ordered."""

    return [f"{file}:{files[file][begin][1]}" for file, begin, _ in sorted(merged)]


def _span_line_range(
    merged: Sequence[tuple[str, int, int]],
    files: Mapping[str, Sequence[_Token]],
    first_file: str,
    first_start: int,
) -> tuple[int, int]:
    """Return the (start, end) source lines of the anchor occurrence."""

    for file, begin, end in merged:
        if file == first_file and begin <= first_start < end:
            return files[file][begin][1], files[file][end - 1][1]
    line = files[first_file][first_start][1]
    return line, line


def _duplication_finding(
    check: DuplicationCheck,
    candidate: _Candidate,
    files: Mapping[str, Sequence[_Token]],
) -> Finding:
    """Build the finding for one accepted clone candidate."""

    length, occurrences, merged = candidate
    first_file, first_start = min(occurrences)
    files_involved = sorted({file for file, _, _ in merged})
    count = len(merged)
    locations = _occurrence_locations(merged, files)
    start_line, end_line = _span_line_range(merged, files, first_file, first_start)
    shown = ", ".join(locations[:5])
    if len(locations) > 5:
        shown += f", and {len(locations) - 5} more"
    return Finding(
        rule_id=check.rule_id,
        check=check.id,
        category=check.category,
        severity=check.default_severity,
        title="Duplicated code block",
        message=(
            f"This {length}-token block is copy-pasted at {count} places "
            f"(here lines {start_line}-{end_line}): {shown}"
        ),
        location=Location(file=first_file, line=start_line),
        symbol=f"{first_file}:{start_line}:{length}",
        properties={
            "tokens": length,
            "occurrences": count,
            "files": files_involved,
            "locations": locations,
        },
        remediation=Remediation(
            summary="Extract the duplicated block into a shared unit",
            detail=(
                "Factor the repeated token sequence into a function or "
                "module and call it from each site so the logic has a "
                "single owner."
            ),
        ),
    )


class DuplicationCheck:
    """Flag token sequences repeated across or within Python files."""

    id: str = "maintainability.duplication"
    rule_id: str = "HT-MAINT-005"
    category: Category = Category.MAINTAINABILITY
    default_severity: Severity = Severity.LOW
    api_version: int = CHECK_API_VERSION
    cross_file: bool = True
    file_kinds: frozenset[FileKind] = frozenset({FileKind.PYTHON})

    def supports(self, ctx: CheckContext) -> bool:
        """Run whenever the context contains at least one Python file."""

        return bool(ctx.files_for(self.file_kinds))

    def run(self, ctx: CheckContext) -> CheckResult:
        """Emit one finding per maximal duplicated token block."""

        min_tokens, min_occurrences, max_findings = _duplication_options(ctx, self.id)

        files = _collect_files(ctx, self.file_kinds, min_tokens)
        groups = _window_groups(files, min_tokens)
        candidates = _candidate_clones(groups, files, min_tokens, min_occurrences)
        candidates.sort(key=lambda candidate: (-candidate[0], _clone_signature(candidate[1])))

        eligible = _select_eligible(candidates)
        accepted = eligible[:max_findings]
        notes = (
            f"duplication findings truncated to {max_findings} of {len(eligible)} groups"
            if len(eligible) > max_findings
            else None
        )

        findings = [_duplication_finding(self, candidate, files) for candidate in accepted]
        findings.sort(key=lambda finding: finding.location.file if finding.location else "")
        return _result(self.id, findings, notes=notes)
