# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added
- Phase 1: typed Pydantic settings (AppSettings, RiskSettings) loading .env + app.yaml + risk.yaml with cross-field validation and live-mode guard.
- Phase 1: SQLAlchemy 2.x ORM models for all 19 DSOR tables (BUILD_PLAN.md §3).
- Phase 1: Alembic initial migration (SQLite + Postgres support).
- Phase 1: Typed repository classes for all DSOR tables.
- Phase 1: SystemStateHelper (mode, kill switch, daily counters).
- Phase 1: Structured JSON logging with secret redaction filter.
- Phase 1: Clock drift check stored in system_state.
- Phase 1: Startup config audit (git SHA + file hash → config_audit table).
- ADR-005: DSOR schema and ORM design decisions.
- Phase 0: repo scaffold, tooling, feasibility analysis.
