"""End-to-end tests for scripts/oversight/signoff_gate.py.

Each test builds a throwaway git repo with a minimal step-manifest, commits files
and stamps in controlled order, and asserts the gate's exit code. The gate's
contract is timestamp-ordering of *git commits*, so the tests drive git directly
rather than mocking.
"""
import subprocess
import textwrap
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parent.parent / "scripts" / "oversight" / "signoff_gate.py"

MANIFEST = textwrap.dedent(
    """
    contract_version: "1"
    role_mappings:
      code-review: code-reviewer
      security: security-reviewer
    steps:
      - id: 1
        required_signoffs:
          - code-review
          - security
    """
).strip()


def git(repo: Path, *args, env=None):
    base_env = {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True, env={**_os_environ(), **base_env}
    )


def _os_environ():
    import os

    return dict(os.environ)


def commit_at(repo: Path, message: str, when: str):
    """Commit staged changes with author+committer date pinned to `when` (ISO)."""
    git(repo, "commit", "-m", message, env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when})


def write(repo: Path, rel: str, content: str):
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def stamp(role: str, status: str = "APPROVED") -> str:
    return f"role: {role}\nagent: x\nstatus: {status}\n"


def run_gate(repo: Path, *args):
    return subprocess.run(
        ["python3", str(GATE), *args],
        cwd=repo,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    write(r, "contract/step-manifest.yaml", MANIFEST)
    git(r, "add", "-A")
    commit_at(r, "base manifest", "2026-01-01T00:00:00")
    git(r, "checkout", "-q", "-b", "feature")
    return r


def test_pass_same_commit(repo):
    """Files and stamps committed together → same timestamp → PASS."""
    write(repo, "app/code.py", "x = 1\n")
    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    write(repo, "signoffs/security.stamp", stamp("security"))
    git(repo, "add", "-A")
    commit_at(repo, "change + stamps", "2026-02-01T00:00:00")

    result = run_gate(repo, "--base", "main")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS" in result.stdout


def test_pass_two_commit(repo):
    """Stamps committed after the code (T2 > T1) → PASS."""
    write(repo, "app/code.py", "x = 1\n")
    git(repo, "add", "-A")
    commit_at(repo, "change", "2026-02-01T00:00:00")

    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    write(repo, "signoffs/security.stamp", stamp("security"))
    git(repo, "add", "-A")
    commit_at(repo, "stamps", "2026-02-01T00:05:00")

    result = run_gate(repo, "--base", "main")
    assert result.returncode == 0, result.stdout + result.stderr


def test_fail_missing_stamp(repo):
    """Only one of two required roles signed → FAIL."""
    write(repo, "app/code.py", "x = 1\n")
    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    git(repo, "add", "-A")
    commit_at(repo, "change + one stamp", "2026-02-01T00:00:00")

    result = run_gate(repo, "--base", "main")
    assert result.returncode == 1
    assert "security" in result.stdout


def test_fail_stale_stamp(repo):
    """Sign off, then commit a newer change without re-signing → FAIL."""
    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    write(repo, "signoffs/security.stamp", stamp("security"))
    git(repo, "add", "-A")
    commit_at(repo, "stamps", "2026-02-01T00:00:00")

    write(repo, "app/code.py", "x = 2\n")
    git(repo, "add", "-A")
    commit_at(repo, "later change, no re-sign", "2026-02-01T01:00:00")

    result = run_gate(repo, "--base", "main")
    assert result.returncode == 1
    assert "STALE" in result.stdout or "older" in result.stdout


def test_fail_bad_status(repo):
    """A stamp with an unrecognised status → FAIL."""
    write(repo, "app/code.py", "x = 1\n")
    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    write(repo, "signoffs/security.stamp", stamp("security", status="ESCALATED"))
    git(repo, "add", "-A")
    commit_at(repo, "change + stamps", "2026-02-01T00:00:00")

    result = run_gate(repo, "--base", "main")
    assert result.returncode == 1


def test_not_applicable_passes(repo):
    """NOT_APPLICABLE counts as a valid, present sign-off."""
    write(repo, "app/code.py", "x = 1\n")
    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    write(repo, "signoffs/security.stamp", stamp("security", status="NOT_APPLICABLE"))
    git(repo, "add", "-A")
    commit_at(repo, "change + stamps", "2026-02-01T00:00:00")

    result = run_gate(repo, "--base", "main")
    assert result.returncode == 0, result.stdout + result.stderr


def test_deploy_mode_all_tracked(repo):
    """--all mode checks every tracked file, not just the diff."""
    write(repo, "app/code.py", "x = 1\n")
    write(repo, "signoffs/code-review.stamp", stamp("code-review"))
    write(repo, "signoffs/security.stamp", stamp("security"))
    git(repo, "add", "-A")
    commit_at(repo, "change + stamps", "2026-02-01T00:00:00")

    result = run_gate(repo, "--all")
    assert result.returncode == 0, result.stdout + result.stderr
