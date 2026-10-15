"""Unit tests for :mod:`healthytoon.core.runner`."""

from __future__ import annotations

from pathlib import Path

import pytest

from healthytoon.config.models import Config
from healthytoon.core.context import CheckContext
from healthytoon.core.protocols import CHECK_API_VERSION, CheckResult
from healthytoon.core.runner import run_checks
from healthytoon.core.types import (
    Category,
    CheckOutcome,
    FileKind,
    Finding,
    Location,
    Severity,
    Status,
    ToolInfo,
    compute_fingerprint,
)

_TOOL = ToolInfo(version="0.1.0")


class FakeCheck:
    """Configurable concrete check satisfying the :class:`Check` protocol."""

    id: str
    category: Category
    default_severity: Severity
    api_version: int
    cross_file: bool = False
    file_kinds: frozenset[FileKind]

    def __init__(
        self,
        check_id: str,
        *,
        findings: tuple[Finding, ...] = (),
        supports: bool = True,
        error: Exception | None = None,
        category: Category = Category.MAINTAINABILITY,
        severity: Severity = Severity.LOW,
        api_version: int = CHECK_API_VERSION,
        file_kinds: frozenset[FileKind] = frozenset(),
    ) -> None:
        self.id = check_id
        self.category = category
        self.default_severity = severity
        self.api_version = api_version
        self.file_kinds = file_kinds
        self.findings = findings
        self._supports = supports
        self._error = error
        self.run_calls = 0

    def supports(self, ctx: CheckContext) -> bool:
        return self._supports

    def run(self, ctx: CheckContext) -> CheckResult:
        self.run_calls += 1
        if self._error is not None:
            raise self._error
        return CheckResult(check=self.id, findings=self.findings)


def _finding(
    rule_id: str,
    *,
    check: str = "fake.a",
    severity: Severity = Severity.LOW,
    category: Category = Category.MAINTAINABILITY,
    file: str = "pkg/a.py",
    line: int | None = 1,
    symbol: str | None = None,
    message: str = "message",
    fingerprint: str = "",
) -> Finding:
    return Finding(
        rule_id=rule_id,
        check=check,
        category=category,
        severity=severity,
        title=f"title for {rule_id}",
        message=message,
        location=Location(file=file, line=line) if file else None,
        symbol=symbol,
        fingerprint=fingerprint,
    )


@pytest.fixture
def ctx(tmp_path: Path) -> CheckContext:
    return CheckContext(root=tmp_path, files=(), config=Config())


def _ctx_with_files(tmp_path: Path, *names: str) -> CheckContext:
    """Build a context whose ``files`` reference ``names`` relative to ``tmp_path``.

    ``files_for`` classifies paths by name/suffix only, so the files do not need
    to exist on disk.
    """

    return CheckContext(
        root=tmp_path, files=tuple(tmp_path / name for name in names), config=Config()
    )


def test_findings_aggregated_and_fingerprints_filled(ctx: CheckContext) -> None:
    first = _finding("HT-A-001", check="fake.a", file="pkg/a.py", symbol="thing", message="msg a")
    second = _finding(
        "HT-B-001",
        check="fake.b",
        file="pkg/b.py",
        severity=Severity.HIGH,
        category=Category.SECURITY,
    )
    checks = (
        FakeCheck("fake.a", findings=(first,)),
        FakeCheck("fake.b", findings=(second,), category=Category.SECURITY),
    )

    report = run_checks(checks, ctx, tool=_TOOL)

    assert len(report.findings) == 2
    assert report.summary.total == 2
    assert report.findings[0].rule_id == "HT-A-001"
    assert report.findings[0].fingerprint == compute_fingerprint(
        rule_id="HT-A-001", file="pkg/a.py", symbol="thing"
    )
    assert report.findings[1].rule_id == "HT-B-001"
    assert report.findings[1].fingerprint == compute_fingerprint(
        rule_id="HT-B-001", file="pkg/b.py", symbol=None
    )
    assert dict(report.summary.by_severity) == {Severity.LOW: 1, Severity.HIGH: 1}
    assert dict(report.summary.by_category) == {Category.MAINTAINABILITY: 1, Category.SECURITY: 1}
    assert report.target.checks_run == ("fake.a", "fake.b")
    assert report.target.path == str(ctx.root)
    assert report.target.files_scanned == 0
    assert report.generated_at is None


def test_duplicate_fingerprints_collapsed(ctx: CheckContext) -> None:
    first = _finding("HT-DUP", check="dup", file="pkg/a.py", line=1, symbol="shared", message="one")
    second = _finding(
        "HT-DUP", check="dup", file="pkg/a.py", line=5, symbol="shared", message="two"
    )
    checks = (FakeCheck("dup", findings=(first,)), FakeCheck("dup", findings=(second,)))

    report = run_checks(checks, ctx, tool=_TOOL)

    assert len(report.findings) == 1
    kept = report.findings[0]
    assert kept.location is not None
    assert kept.location.line == 1
    assert kept.fingerprint == compute_fingerprint(
        rule_id="HT-DUP", file="pkg/a.py", symbol="shared"
    )
    assert report.summary.total == 1


def test_existing_fingerprint_is_preserved(ctx: CheckContext) -> None:
    stamped = _finding("HT-F-001", check="stamped", fingerprint="preset-value")

    report = run_checks((FakeCheck("stamped", findings=(stamped,)),), ctx, tool=_TOOL)

    assert report.findings[0].fingerprint == "preset-value"


def test_location_less_finding_fingerprint_uses_empty_path(ctx: CheckContext) -> None:
    bare = _finding("HT-BARE", check="bare", file="")

    report = run_checks((FakeCheck("bare", findings=(bare,)),), ctx, tool=_TOOL)

    assert report.findings[0].fingerprint == compute_fingerprint(
        rule_id="HT-BARE", file="", symbol=None
    )


def test_raising_check_yields_error_outcome_without_finding(ctx: CheckContext) -> None:
    broken = FakeCheck("broken", category=Category.TESTING, error=RuntimeError("kaboom"))
    healthy = FakeCheck("healthy", findings=(_finding("HT-H-001", check="healthy"),))

    report = run_checks((broken, healthy), ctx, tool=_TOOL)

    assert len(report.findings) == 1
    assert report.findings[0].rule_id == "HT-H-001"
    assert report.summary.total == 1
    outcomes = {outcome.check: outcome for outcome in report.outcomes}
    assert outcomes["broken"].status is Status.ERROR
    assert outcomes["broken"].message == "RuntimeError: kaboom"
    assert outcomes["broken"].findings == 0
    assert outcomes["healthy"].status is Status.PASS
    assert report.analysis_incomplete is True
    assert report.target.checks_run == ("broken", "healthy")
    assert broken.run_calls == 1


def test_unsupported_check_is_skipped(ctx: CheckContext) -> None:
    skipped = FakeCheck(
        "skipped",
        supports=False,
        findings=(_finding("HT-S-001", check="skipped"),),
    )
    healthy = FakeCheck("healthy", findings=(_finding("HT-H-001", check="healthy"),))

    report = run_checks((skipped, healthy), ctx, tool=_TOOL)

    assert [finding.rule_id for finding in report.findings] == ["HT-H-001"]
    outcomes = {outcome.check: outcome for outcome in report.outcomes}
    assert outcomes["skipped"].status is Status.SKIP
    assert outcomes["skipped"].message == "not applicable"
    assert "skipped" not in report.target.checks_run
    assert report.target.checks_run == ("healthy",)
    assert report.analysis_incomplete is False
    assert skipped.run_calls == 0


def test_api_version_mismatch_is_skipped(ctx: CheckContext) -> None:
    stale = FakeCheck(
        "stale",
        api_version=CHECK_API_VERSION + 1,
        findings=(_finding("HT-X-001", check="stale"),),
    )

    report = run_checks((stale,), ctx, tool=_TOOL)

    assert report.findings == ()
    assert len(report.outcomes) == 1
    assert report.outcomes[0].status is Status.SKIP
    assert report.outcomes[0].message == f"incompatible check API version {CHECK_API_VERSION + 1}"
    assert report.target.checks_run == ()
    assert stale.run_calls == 0


def test_matching_file_kinds_run(tmp_path: Path) -> None:
    ctx = _ctx_with_files(tmp_path, "pkg/mod.py")
    check = FakeCheck("py-only", file_kinds=frozenset({FileKind.PYTHON}))

    report = run_checks((check,), ctx, tool=_TOOL)

    assert report.outcomes == (CheckOutcome("py-only", Status.PASS),)
    assert report.target.checks_run == ("py-only",)
    assert check.run_calls == 1


def test_non_matching_file_kinds_skip(tmp_path: Path) -> None:
    ctx = _ctx_with_files(tmp_path, "README.md")
    check = FakeCheck(
        "py-only",
        file_kinds=frozenset({FileKind.PYTHON}),
        findings=(_finding("HT-P-001", check="py-only"),),
    )

    report = run_checks((check,), ctx, tool=_TOOL)

    assert report.findings == ()
    assert report.outcomes == (CheckOutcome("py-only", Status.SKIP, message="no matching files"),)
    assert report.target.checks_run == ()
    assert check.run_calls == 0


def test_empty_file_kinds_runs_even_without_files(ctx: CheckContext) -> None:
    check = FakeCheck(
        "any-files",
        file_kinds=frozenset(),
        findings=(_finding("HT-A-001", check="any-files"),),
    )

    report = run_checks((check,), ctx, tool=_TOOL)

    assert [finding.rule_id for finding in report.findings] == ["HT-A-001"]
    assert report.outcomes == (CheckOutcome("any-files", Status.PASS, findings=1),)
    assert report.target.checks_run == ("any-files",)


def test_empty_checks_gives_perfect_score(ctx: CheckContext) -> None:
    report = run_checks((), ctx, tool=_TOOL)

    assert report.findings == ()
    assert report.outcomes == ()
    assert report.summary.total == 0
    assert report.summary.score.overall == 100.0
    assert report.summary.score.grade == "A"
    assert report.target.checks_run == ()


def test_outcomes_sorted_by_check_id(ctx: CheckContext) -> None:
    checks = (FakeCheck("zeta"), FakeCheck("alpha"), FakeCheck("mid", supports=False))

    report = run_checks(checks, ctx, tool=_TOOL)

    assert [outcome.check for outcome in report.outcomes] == ["alpha", "mid", "zeta"]


def test_reversed_check_order_yields_identical_report(ctx: CheckContext) -> None:
    checks = (
        FakeCheck("fake.a", findings=(_finding("HT-A-001", check="fake.a", file="pkg/a.py"),)),
        FakeCheck(
            "fake.b",
            category=Category.SECURITY,
            severity=Severity.HIGH,
            findings=(
                _finding(
                    "HT-B-001",
                    check="fake.b",
                    file="pkg/b.py",
                    category=Category.SECURITY,
                    severity=Severity.HIGH,
                ),
            ),
        ),
        FakeCheck("broken", error=ValueError("nope")),
        FakeCheck("skipped", supports=False),
    )

    forward = run_checks(checks, ctx, tool=_TOOL)
    backward = run_checks(tuple(reversed(checks)), ctx, tool=_TOOL)

    assert forward == backward
    assert forward.target.checks_run == ("broken", "fake.a", "fake.b")
    assert [outcome.check for outcome in forward.outcomes] == [
        "broken",
        "fake.a",
        "fake.b",
        "skipped",
    ]


def test_jobs_argument_is_accepted(ctx: CheckContext) -> None:
    checks = (FakeCheck("fake.a", findings=(_finding("HT-A-001", check="fake.a"),)),)

    auto = run_checks(checks, ctx, tool=_TOOL, jobs=0)
    parallel = run_checks(checks, ctx, tool=_TOOL, jobs=4)

    assert auto == parallel
    assert auto.summary.total == 1
