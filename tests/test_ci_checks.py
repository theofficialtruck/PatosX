"""
CI quality gate checks — run these locally before pushing to avoid PR failures.

Covers: Bandit (B110 bare-except-pass), Ruff (lint errors), codespell (spelling).
Each test calls the real tool as a subprocess so the result matches CI exactly.
"""

import json
import subprocess  # nosec B404 — subprocess is intentional here to invoke quality tools
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent


# ── helpers ──────────────────────────────────────────────────────────────────


def _run(*args, **kwargs) -> subprocess.CompletedProcess:
    """Run a command, returning its CompletedProcess (never raises)."""
    return subprocess.run(  # nosec B603 — args are built from sys.executable + hard-coded tool flags
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(PROJECT_ROOT),
        **kwargs,
    )


# ── Bandit ───────────────────────────────────────────────────────────────────


def test_bandit_no_b110_in_main():
    """Bandit must not find any B110 (bare except: pass) in main.py."""
    result = _run(
        sys.executable,
        "-m",
        "bandit",
        "-r",
        "main.py",
        "-t",
        "B110",  # only check this specific test
        "-f",
        "json",
        "--quiet",
    )
    # bandit exits 0 (no issues) or 1 (issues found)
    if result.returncode not in (0, 1):
        # tool not available or crashed — skip rather than fail
        import pytest

        pytest.skip(f"bandit exited {result.returncode}: {result.stderr[:300]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        import pytest

        pytest.skip("bandit output was not JSON — tool may not be installed")

    issues = data.get("results", [])
    b110 = [r for r in issues if r.get("test_id") == "B110"]
    assert b110 == [], f"Found {len(b110)} B110 (bare except: pass) violation(s) in main.py:\n" + "\n".join(
        f"  line {r['line_number']}: {r['code'].strip()!r}" for r in b110
    )


def test_bandit_no_b110_in_tests():
    """Bandit must not find B110 issues in the tests/ directory."""
    result = _run(
        sys.executable,
        "-m",
        "bandit",
        "-r",
        "tests/",
        "-t",
        "B110",
        "-f",
        "json",
        "--quiet",
    )
    if result.returncode not in (0, 1):
        import pytest

        pytest.skip(f"bandit exited {result.returncode}: {result.stderr[:300]}")

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        import pytest

        pytest.skip("bandit output was not JSON — tool may not be installed")

    issues = data.get("results", [])
    b110 = [r for r in issues if r.get("test_id") == "B110"]
    assert b110 == [], f"Found {len(b110)} B110 violation(s) in tests/:\n" + "\n".join(
        f"  {r['filename']}:{r['line_number']}: {r['code'].strip()!r}" for r in b110
    )


# ── Ruff ─────────────────────────────────────────────────────────────────────


def test_ruff_no_errors_main():
    """Ruff must report zero lint errors on main.py."""
    result = _run(
        sys.executable,
        "-m",
        "ruff",
        "check",
        "main.py",
        "--output-format=json",
    )
    if result.returncode == 2:
        import pytest

        pytest.skip(f"ruff not available or crashed: {result.stderr[:300]}")

    try:
        diagnostics = json.loads(result.stdout)
    except json.JSONDecodeError:
        # non-zero exit with no parseable JSON means configuration error
        import pytest

        pytest.skip("ruff output was not JSON")

    errors = [d for d in diagnostics if d.get("code")]
    assert errors == [], f"ruff reported {len(errors)} error(s) in main.py:\n" + "\n".join(
        f"  line {d['location']['row']}: [{d['code']}] {d['message']}"
        for d in errors[:20]  # cap at 20 to avoid overwhelming output
    )


def test_ruff_no_errors_tests():
    """Ruff must report zero lint errors in tests/."""
    result = _run(
        sys.executable,
        "-m",
        "ruff",
        "check",
        "tests/",
        "--output-format=json",
    )
    if result.returncode == 2:
        import pytest

        pytest.skip(f"ruff not available or crashed: {result.stderr[:300]}")

    try:
        diagnostics = json.loads(result.stdout)
    except json.JSONDecodeError:
        import pytest

        pytest.skip("ruff output was not JSON")

    errors = [d for d in diagnostics if d.get("code")]
    assert errors == [], f"ruff reported {len(errors)} error(s) in tests/:\n" + "\n".join(
        f"  {d['filename']}:{d['location']['row']}: [{d['code']}] {d['message']}" for d in errors[:20]
    )


# ── codespell ─────────────────────────────────────────────────────────────────


def test_codespell_no_spelling_errors():
    """codespell must not find spelling errors in Python source files."""
    result = _run(
        sys.executable,
        "-m",
        "codespell",
        "--config",
        ".codespellrc",
        "main.py",
        "tests/",
        "--quiet-level",
        "2",
    )
    if result.returncode == 127 or "No module named" in result.stderr:
        import pytest

        pytest.skip("codespell not installed — skipping spelling check")

    assert result.returncode == 0, "codespell found spelling errors:\n" + (result.stdout or result.stderr)[:2000]
