"""Tests for the PreToolUse guard hooks in .claude/hooks/.

Claude Code only blocks a tool call when a PreToolUse hook exits with code 2;
exit 1 is a non-blocking error and the tool still runs. Every block assertion
therefore checks ``== 2`` exactly.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).parent.parent / ".claude" / "hooks"
HOOK = HOOKS_DIR / "block_bash_writes.py"
SENSITIVE_HOOK = HOOKS_DIR / "block_sensitive.sh"


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
    """Commands that must be blocked (exit 2 — the only blocking code)."""

    # sed -i
    def test_sed_i_env(self) -> None:
        rc, err = _run("sed -i 's/foo/bar/' .env")
        assert rc == 2
        assert "BLOCKED" in err

    def test_sed_inplace_risk_yaml(self) -> None:
        rc, err = _run("sed --in-place 's/2.0/5.0/' config/risk.yaml")
        assert rc == 2
        assert "BLOCKED" in err

    # output redirects
    def test_redirect_overwrite_env(self) -> None:
        rc, err = _run('echo "KEY=val" > .env')
        assert rc == 2
        assert "BLOCKED" in err

    def test_redirect_append_env(self) -> None:
        rc, err = _run('echo "KEY=val" >> .env')
        assert rc == 2
        assert "BLOCKED" in err

    def test_redirect_risk_yaml(self) -> None:
        rc, err = _run("cat tmp.yaml > config/risk.yaml")
        assert rc == 2
        assert "BLOCKED" in err

    def test_redirect_settings_json(self) -> None:
        rc, err = _run('echo "{}" > .claude/settings.json')
        assert rc == 2
        assert "BLOCKED" in err

    # tee
    def test_tee_env(self) -> None:
        rc, err = _run("echo KEY=val | tee .env")
        assert rc == 2
        assert "BLOCKED" in err

    def test_tee_live_approval(self) -> None:
        rc, err = _run("cat approval.yaml | tee config/live_approval.yaml")
        assert rc == 2
        assert "BLOCKED" in err

    # mv / cp targeting protected dest
    def test_mv_to_env(self) -> None:
        rc, err = _run("mv .env.new .env")
        assert rc == 2
        assert "BLOCKED" in err

    def test_cp_to_risk_yaml(self) -> None:
        rc, err = _run("cp /tmp/risk.yaml config/risk.yaml")
        assert rc == 2
        assert "BLOCKED" in err

    # rm
    def test_rm_env(self) -> None:
        rc, err = _run("rm .env")
        assert rc == 2
        assert "BLOCKED" in err

    def test_rm_live_approval(self) -> None:
        rc, err = _run("rm config/live_approval.yaml")
        assert rc == 2
        assert "BLOCKED" in err

    # git checkout / restore
    def test_git_checkout_env(self) -> None:
        rc, err = _run("git checkout -- .env")
        assert rc == 2
        assert "BLOCKED" in err

    def test_git_restore_risk_yaml(self) -> None:
        rc, err = _run("git restore config/risk.yaml")
        assert rc == 2
        assert "BLOCKED" in err

    def test_git_checkout_live_approval(self) -> None:
        rc, err = _run("git checkout -- config/live_approval.yaml")
        assert rc == 2
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
        assert rc == 2
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

    def test_git_checkout_branch(self) -> None:
        # git checkout <branchname> — not a file restore
        rc, _ = _run("git checkout main")
        assert rc == 0

    def test_git_restore_safe_file(self) -> None:
        rc, _ = _run("git restore src/trader/config/settings.py")
        assert rc == 0


def _run_raw(stdin: str) -> tuple[int, str]:
    """Run the Bash hook with arbitrary stdin; return (returncode, stderr)."""
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr.strip()


class TestBashHookFailClosed:
    """Malformed or missing input must block (exit 2), never allow."""

    def test_invalid_json(self) -> None:
        rc, err = _run_raw("not json")
        assert rc == 2
        assert "BLOCKED" in err

    def test_empty_stdin(self) -> None:
        rc, err = _run_raw("")
        assert rc == 2
        assert "BLOCKED" in err

    def test_non_object_json(self) -> None:
        rc, err = _run_raw("[1, 2, 3]")
        assert rc == 2
        assert "BLOCKED" in err

    def test_missing_tool_name(self) -> None:
        rc, err = _run_raw(json.dumps({"tool_input": {"command": "ls"}}))
        assert rc == 2
        assert "BLOCKED" in err

    def test_missing_tool_input(self) -> None:
        rc, err = _run_raw(json.dumps({"tool_name": "Bash"}))
        assert rc == 2
        assert "BLOCKED" in err

    def test_missing_command(self) -> None:
        rc, err = _run_raw(json.dumps({"tool_name": "Bash", "tool_input": {}}))
        assert rc == 2
        assert "BLOCKED" in err

    def test_non_string_command(self) -> None:
        rc, err = _run_raw(json.dumps({"tool_name": "Bash", "tool_input": {"command": 1}}))
        assert rc == 2
        assert "BLOCKED" in err

    def test_empty_command(self) -> None:
        rc, err = _run("")
        assert rc == 2
        assert "BLOCKED" in err

    def test_append_comment_to_risk_yaml(self) -> None:
        rc, err = _run('echo "# x" >> config/risk.yaml')
        assert rc == 2
        assert "config/risk.yaml" in err


def _run_sensitive(stdin: str) -> tuple[int, str]:
    """Run block_sensitive.sh with arbitrary stdin; return (returncode, stderr)."""
    result = subprocess.run(
        ["bash", str(SENSITIVE_HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stderr.strip()


def _edit_payload(file_path: str, tool: str = "Edit", key: str = "file_path") -> str:
    return json.dumps({"tool_name": tool, "tool_input": {key: file_path}})


class TestSensitiveHookBlocks:
    """Edit/Write/MultiEdit/NotebookEdit on protected files must exit 2."""

    def test_edit_risk_yaml(self) -> None:
        rc, err = _run_sensitive(_edit_payload("config/risk.yaml"))
        assert rc == 2
        assert "BLOCKED" in err

    def test_edit_risk_yaml_absolute(self) -> None:
        rc, err = _run_sensitive(_edit_payload("/Users/x/ai-trader/config/risk.yaml"))
        assert rc == 2
        assert "BLOCKED" in err

    def test_edit_risk_yaml_dot_segments(self) -> None:
        rc, err = _run_sensitive(_edit_payload("./config/./risk.yaml"))
        assert rc == 2
        assert "BLOCKED" in err

    def test_write_env(self) -> None:
        rc, err = _run_sensitive(_edit_payload(".env", tool="Write"))
        assert rc == 2
        assert "BLOCKED" in err

    def test_multiedit_live_approval(self) -> None:
        rc, err = _run_sensitive(_edit_payload("config/live_approval.yaml", tool="MultiEdit"))
        assert rc == 2
        assert "BLOCKED" in err

    def test_edit_settings_json(self) -> None:
        rc, err = _run_sensitive(_edit_payload(".claude/settings.json"))
        assert rc == 2
        assert "BLOCKED" in err

    def test_edit_hook_scripts(self) -> None:
        for p in (".claude/hooks/block_sensitive.sh", ".claude/hooks/block_bash_writes.py"):
            rc, err = _run_sensitive(_edit_payload(p))
            assert rc == 2
            assert "BLOCKED" in err

    def test_notebook_edit_notebook_path(self) -> None:
        rc, err = _run_sensitive(
            _edit_payload("config/risk.yaml", tool="NotebookEdit", key="notebook_path")
        )
        assert rc == 2
        assert "BLOCKED" in err


class TestSensitiveHookFailClosed:
    """Malformed or missing input must block (exit 2), never allow."""

    def test_empty_stdin(self) -> None:
        rc, err = _run_sensitive("")
        assert rc == 2
        assert "BLOCKED" in err

    def test_invalid_json(self) -> None:
        rc, err = _run_sensitive("not json")
        assert rc == 2
        assert "BLOCKED" in err

    def test_missing_tool_input(self) -> None:
        rc, err = _run_sensitive(json.dumps({"tool_name": "Edit"}))
        assert rc == 2
        assert "BLOCKED" in err

    def test_missing_file_path(self) -> None:
        rc, err = _run_sensitive(json.dumps({"tool_name": "Edit", "tool_input": {}}))
        assert rc == 2
        assert "BLOCKED" in err

    def test_non_string_file_path(self) -> None:
        rc, err = _run_sensitive(json.dumps({"tool_name": "Edit", "tool_input": {"file_path": 5}}))
        assert rc == 2
        assert "BLOCKED" in err


class TestSensitiveHookAllows:
    """Unprotected files must be allowed (exit 0)."""

    def test_env_example(self) -> None:
        rc, _ = _run_sensitive(_edit_payload(".env.example"))
        assert rc == 0

    def test_source_file(self) -> None:
        rc, _ = _run_sensitive(_edit_payload("src/trader/config/settings.py"))
        assert rc == 0

    def test_other_config(self) -> None:
        rc, _ = _run_sensitive(_edit_payload("config/symbols.yaml"))
        assert rc == 0
