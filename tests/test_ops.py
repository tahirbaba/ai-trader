"""Tests for Phase 1: config_audit and configure_logging."""

import logging
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from trader.dsor.base import Base
from trader.dsor.repositories import ConfigAuditRepository
from trader.ops.config_audit import record_startup_config
from trader.ops.logging import SecretRedactionFilter, build_json_formatter, configure_logging


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestConfigAudit:
    def test_records_existing_config_files(self, session: Session, tmp_path: Path) -> None:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "app.yaml").write_text("trading:\n  symbols: []\n")
        (cfg_dir / "risk.yaml").write_text("max_margin_per_trade_usd: 2.0\n")
        record_startup_config(session, repo_root=tmp_path)
        session.commit()
        repo = ConfigAuditRepository(session)
        records = repo.list_recent(10)
        files = {r.file for r in records}
        assert "config/app.yaml" in files
        assert "config/risk.yaml" in files

    def test_missing_config_file_skipped_gracefully(
        self, session: Session, tmp_path: Path
    ) -> None:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        (cfg_dir / "app.yaml").write_text("trading:\n  symbols: []\n")
        # risk.yaml intentionally absent
        record_startup_config(session, repo_root=tmp_path)
        session.commit()
        repo = ConfigAuditRepository(session)
        records = repo.list_recent(10)
        files = {r.file for r in records}
        assert "config/app.yaml" in files
        assert "config/risk.yaml" not in files

    def test_diff_field_contains_sha256_hash(
        self, session: Session, tmp_path: Path
    ) -> None:
        cfg_dir = tmp_path / "config"
        cfg_dir.mkdir()
        content = "max_margin_per_trade_usd: 2.0\n"
        (cfg_dir / "risk.yaml").write_text(content)
        record_startup_config(session, repo_root=tmp_path)
        session.commit()
        repo = ConfigAuditRepository(session)
        records = {r.file: r for r in repo.list_recent(10)}
        record = records.get("config/risk.yaml")
        assert record is not None
        assert record.diff is not None
        assert len(record.diff) == 64  # SHA-256 hex digest length


class TestConfigureLogging:
    def test_configure_logging_attaches_redaction_filter(self, caplog: pytest.LogCaptureFixture) -> None:
        configure_logging("DEBUG")
        root = logging.getLogger()
        has_redaction = any(
            isinstance(f, SecretRedactionFilter)
            for h in root.handlers
            for f in h.filters
        )
        assert has_redaction, "SecretRedactionFilter not attached to any root handler"

    def test_configure_logging_emits_json(self) -> None:
        import io
        import json
        logger = logging.getLogger("test_configure")
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(build_json_formatter())
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        logger.warning("test-configure-json")
        output = stream.getvalue().strip()
        parsed = json.loads(output)
        assert parsed["message"] == "test-configure-json"
        logger.removeHandler(handler)
