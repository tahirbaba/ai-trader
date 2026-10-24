"""Tests for .claude/hooks/block_bash_writes.py — PreToolUse Bash guard."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent / ".claude" / "hooks" / "block_bash_writes.py"


def _run(command: str) -> tuple[int, str]:
    """Run the hook with a Bash tool input; return (returncode, stderr)."""
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=payload,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr.strip()


class TestBlockedCommands:
    """Commands that must be blocked (exit 1)."""

    # sed -i
    def test_sed_i_env(self) -> None:
        rc, err = _run("sed -i 's/foo/bar/' .env")
        assert rc == 1
        assert "BLOCKED" in err

    def test_sed_inplace_risk_yaml(self) -> None:
        rc, err = _run("sed --in-place 's/2.0/5.0/' config/risk.yaml")
        assert rc == 1
        assert "BLOCKED" in err

    # output redirects
    def test_redirect_overwrite_env(self) -> None:
        rc, err = _run('echo "KEY=val" > .env')
        assert rc == 1
        assert "BLOCKED" in err

    def test_redirect_append_env(self) -> None:
        rc, err = _run('echo "KEY=val" >> .env')
        assert rc == 1
        assert "BLOCKED" in err

    def test_redirect_risk_yaml(self) -> None:
        rc, err = _run("cat tmp.yaml > config/risk.yaml")
        assert rc == 1
        assert "BLOCKED" in err

    def test_redirect_settings_json(self) -> None:
        rc, err = _run('echo "{}" > .claude/settings.json')
        assert rc == 1
        assert "BLOCKED" in err

    # tee
    def test_tee_env(self) -> None:
        rc, err = _run("echo KEY=val | tee .env")
        assert rc == 1
        assert "BLOCKED" in err

    def test_tee_live_approval(self) -> None:
        rc, err = _run("cat approval.yaml | tee config/live_approval.yaml")
        assert rc == 1
        assert "BLOCKED" in err

    # mv / cp targeting protected dest
    def test_mv_to_env(self) -> None:
        rc, err = _run("mv .env.new .env")
        assert rc == 1
        assert "BLOCKED" in err

    def test_cp_to_risk_yaml(self) -> None:
        rc, err = _run("cp /tmp/risk.yaml config/risk.yaml")
        assert rc == 1
        assert "BLOCKED" in err

    # rm
    def test_rm_env(self) -> None:
        rc, err = _run("rm .env")
        assert rc == 1
        assert "BLOCKED" in err

    def test_rm_live_approval(self) -> None:
        rc, err = _run("rm config/live_approval.yaml")
        assert rc == 1
        assert "BLOCKED" in err

    # git checkout / restore
    def test_git_checkout_env(self) -> None:
        rc, err = _run("git checkout -- .env")
        assert rc == 1
        assert "BLOCKED" in err

    def test_git_restore_risk_yaml(self) -> None:
        rc, err = _run("git restore config/risk.yaml")
        assert rc == 1
        assert "BLOCKED" in err

    def test_git_checkout_live_approval(self) -> None:
        rc, err = _run("git checkout -- config/live_approval.yaml")
        assert rc == 1
        assert "BLOCKED" in err


class TestAllowedCommands:
    """Commands that must NOT be blocked (exit 0)."""

    def test_cat_env_example(self) -> None:
        rc, _ = _run("cat .env.example")
        assert rc == 0

    def test_redirect_to_safe_file(self) -> None:
        rc, _ = _run("echo hello > /tmp/output.txt")
        assert rc == 0

    def test_sed_on_safe_file(self) -> None:
        rc, _ = _run("sed -i 's/foo/bar/' src/trader/config/settings.py")
        assert rc == 0

    def test_rm_safe_file(self) -> None:
        rc, _ = _run("rm /tmp/foo.txt")
        assert rc == 0

    def test_mv_safe_files(self) -> None:
        rc, _ = _run("mv /tmp/a.txt /tmp/b.txt")
        assert rc == 0

    def test_cp_env_source_also_blocked(self) -> None:
        # cp FROM .env is blocked too — any cp/mv/rm touching .env is denied.
        # This is intentionally conservative: .env contains secrets and should
        # not be readable/copyable via Claude-run shell commands.
        rc, err = _run("cp .env /tmp/.env.bak")
        assert rc == 1
        assert "BLOCKED" in err

    def test_stderr_redirect_is_safe(self) -> None:
        # 2>&1 or >&2 should not be flagged
        rc, _ = _run("some_cmd 2>&1 | tee /tmp/log.txt")
        assert rc == 0

    def test_non_bash_tool_ignored(self) -> None:
        payload = json.dumps({"tool_name": "Edit", "tool_input": {"file_path": ".env"}})
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=payload,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_empty_command_allowed(self) -> None:
        rc, _ = _run("")
        assert rc == 0

    def test_git_checkout_branch(self) -> None:
        # git checkout <branchname> — not a file restore
        rc, _ = _run("git checkout main")
        assert rc == 0

    def test_git_restore_safe_file(self) -> None:
        rc, _ = _run("git restore src/trader/config/settings.py")
        assert rc == 0
