"""KSOR LessonWriter — the only component allowed to create/modify /ksor/lessons/ files.

Every write produces a local git commit (not pushed) with a descriptive message and
linked trade IDs for full auditability.

Path guard: raises ValueError if the computed output path is outside ksor_root/lessons/.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any

import frontmatter as fm_lib

logger = logging.getLogger(__name__)

# Default ksor_root relative to this file: repo_root/ksor
_DEFAULT_KSOR_ROOT = Path(__file__).resolve().parent.parent.parent.parent / "ksor"


class LessonWriter:
    """Creates and modifies lesson files under ``ksor_root/lessons/``.

    Parameters
    ----------
    ksor_root:
        Root of the KSOR directory tree.  Defaults to the repo's ``/ksor`` directory.
        Tests should pass a temp directory here so no real files are modified.
    """

    def __init__(self, ksor_root: Path | None = None) -> None:
        self._ksor_root = (ksor_root or _DEFAULT_KSOR_ROOT).resolve()
        self._lessons_dir = self._ksor_root / "lessons"

    def write(
        self,
        filename: str,
        frontmatter: dict[str, Any],
        body: str,
        commit_message: str,
        trade_ids: list[str],
    ) -> Path:
        """Write a lesson file and commit it to the local git repo.

        Parameters
        ----------
        filename:
            Filename only (no directory component, no path traversal).
            Must resolve to a path inside ``ksor_root/lessons/``.
        frontmatter:
            Dict of YAML frontmatter fields.
        body:
            Markdown body text (after the frontmatter).
        commit_message:
            Base message for the git commit.
        trade_ids:
            Trade IDs linked to this lesson (appended to the commit message).

        Returns
        -------
        Path:
            Absolute path of the written file.

        Raises
        ------
        ValueError:
            If the resolved output path is outside ``ksor_root/lessons/``.
        """
        # Resolve and validate the output path
        out_path = self._resolve_and_guard(filename)

        # Ensure lessons dir exists
        self._lessons_dir.mkdir(parents=True, exist_ok=True)

        # Build the markdown content
        content = self._render(frontmatter, body)

        # Write file
        out_path.write_text(content, encoding="utf-8")
        logger.info("LessonWriter: wrote %s", out_path)

        # Build full commit message
        full_message = self._build_commit_message(commit_message, trade_ids)

        # Git add + commit
        self._git_commit(out_path, full_message)

        return out_path

    # ------------------------------------------------------------------
    # Path guard
    # ------------------------------------------------------------------

    def _resolve_and_guard(self, filename: str) -> Path:
        """Resolve filename relative to lessons dir and verify it stays inside."""
        # Strip any leading path separators / ".." components by using Path
        # and resolving relative to lessons dir.
        candidate = (self._lessons_dir / filename).resolve()

        # Strict containment check
        try:
            candidate.relative_to(self._lessons_dir)
        except ValueError as exc:
            raise ValueError(
                f"Output path {candidate} is outside the lessons directory "
                f"{self._lessons_dir}. Path guard triggered."
            ) from exc

        return candidate

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @staticmethod
    def _render(fm: dict[str, Any], body: str) -> str:
        """Render frontmatter + body as a .md string."""
        # Use python-frontmatter's Post class for round-trip safe rendering
        post = fm_lib.Post(content=body, **fm)
        return fm_lib.dumps(post)

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    @staticmethod
    def _build_commit_message(message: str, trade_ids: list[str]) -> str:
        if trade_ids:
            ids_str = ", ".join(trade_ids)
            return f"{message}\n\nLinked trades: {ids_str}"
        return message

    def _git_commit(self, file_path: Path, message: str) -> None:
        """Stage and commit a single file in the local git repo."""
        # Find the git repo root (walk up from ksor_root)
        repo_root = self._find_git_root(self._ksor_root)
        if repo_root is None:
            logger.warning(
                "LessonWriter: no git repo found above %s; skipping commit", self._ksor_root
            )
            return

        try:
            subprocess.run(
                ["git", "add", str(file_path)],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
                timeout=30,
            )
            subprocess.run(
                ["git", "commit", "-m", message],
                cwd=str(repo_root),
                check=True,
                capture_output=True,
                timeout=30,
            )
            logger.info("LessonWriter: committed %s", file_path.name)
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.decode(errors="replace") if exc.stderr else ""
            logger.error("LessonWriter: git commit failed: %s", stderr)
            raise

    @staticmethod
    def _find_git_root(start: Path) -> Path | None:
        """Walk up from *start* until we find a .git directory."""
        current = start.resolve()
        for _ in range(20):
            if (current / ".git").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return None
