"""Integration tests: the installed CLI as a real subprocess.

These tests exercise the console entry point end-to-end, including process
exit codes, stream separation, and byte-level output determinism across
different ``PYTHONHASHSEED`` values (ROADMAP T1.1/T1.2 acceptance).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: Distinct hash seeds; identical stdout under all of them proves the report
#: does not depend on Python's randomized string hashing.
HASH_SEEDS: tuple[str, ...] = ("0", "1", "424242")


def _make_repo(root: Path) -> Path:
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (root / "pkg" / "mod.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    (root / "README.md").write_text("# demo\n", encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.0.1"\n', encoding="utf-8"
    )
    return root


def _run_check(repo: Path, hash_seed: str) -> subprocess.CompletedProcess[bytes]:
    """Run ``healthytoon check`` as a subprocess under a fixed ``PYTHONHASHSEED``."""

    env = os.environ.copy()
    env["PYTHONHASHSEED"] = hash_seed
    return subprocess.run(
        [sys.executable, "-m", "healthytoon", "check", str(repo), "--format", "json"],
        capture_output=True,
        env=env,
        check=False,
    )


def test_json_output_is_byte_identical_across_hash_seeds(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    results = [_run_check(repo, seed) for seed in HASH_SEEDS]

    for seed, result in zip(HASH_SEEDS, results, strict=True):
        assert result.returncode == 0, f"seed {seed} stderr: {result.stderr!r}"
    stdouts = {result.stdout for result in results}
    assert len(stdouts) == 1, "JSON stdout must not depend on PYTHONHASHSEED"


def test_stdout_carries_only_the_json_document(tmp_path: Path) -> None:
    repo = _make_repo(tmp_path)

    result = _run_check(repo, "0")

    assert result.returncode == 0, result.stderr
    # stdout and stderr are separate pipes; anything interleaved into stdout
    # (progress bars, warnings, tracebacks) would break a strict full-document
    # parse, so a successful parse proves the streams did not intermix.
    payload = json.loads(result.stdout.decode("utf-8"))
    assert payload["target"]["files_scanned"] == 4
    # The fixture repo has real issues (missing docstrings and annotations),
    # so the registered built-in checks must surface findings on stdout.
    assert payload["summary"]["total"] > 0
    rule_ids = {finding["rule_id"] for finding in payload["findings"]}
    assert {"HT-DOC-001", "HT-TYPE-001"} <= rule_ids
    if result.stderr:
        leaked = result.stderr.decode("utf-8", errors="replace").strip()
        assert leaked not in result.stdout.decode("utf-8", errors="replace")
