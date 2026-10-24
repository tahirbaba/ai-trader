"""Phase 1: Record git SHA + config file hashes at startup."""

from __future__ import annotations

import datetime
import hashlib
import subprocess
from pathlib import Path

from sqlalchemy.orm import Session

from trader.dsor.models import ConfigAudit
from trader.dsor.repositories import ConfigAuditRepository

_CONFIG_FILES = ["config/app.yaml", "config/risk.yaml"]


def _git_head_sha(repo_root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def record_startup_config(session: Session, repo_root: Path | None = None) -> None:
    """Write a ConfigAudit row for each config file at startup."""
    if repo_root is None:
        repo_root = Path(__file__).parent.parent.parent.parent

    git_sha = _git_head_sha(repo_root)
    repo = ConfigAuditRepository(session)
    now = datetime.datetime.utcnow()

    for rel_path in _CONFIG_FILES:
        full_path = repo_root / rel_path
        if not full_path.exists():
            continue
        file_hash = _sha256_file(full_path)
        repo.add(
            ConfigAudit(
                ts=now,
                file=rel_path,
                git_sha=git_sha,
                diff=file_hash,
            )
        )
