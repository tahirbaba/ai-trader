# RUNBOOK — ai-trader (paper mode)

Everything below runs **PAPER mode only**. `trader.main` refuses to start unless
`TRADING_MODE=paper`, and it only ever wires `PaperBroker`. No command, bot or
endpoint can enable live trading.

## 0. Environment variables (`.env`, never committed)

| Variable | Required | Purpose |
|---|---|---|
| `TRADING_MODE` | yes | must be `paper` |
| `ANTHROPIC_API_KEY` | yes | LLM agents |
| `DATABASE_URL` | no | default `sqlite:///./trader.db` |
| `TELEGRAM_BOT_TOKEN` | no | alerts + commands (disabled if empty) |
| `TELEGRAM_CHAT_ID` | if token set | owner's **numeric private** chat id (= user id). Groups are refused; messages from any other chat or sender are ignored |
| `HEALTHCHECK_PING_URL` | strongly recommended | external dead-man ping every 60 s |
| `DASHBOARD_PASSWORD` | no | adds HTTP basic auth to the dashboard |
| `BACKUP_DIR` | no | default `./backups` |
| `BACKUP_RSYNC_TARGET` | no | e.g. `user@host:/backups/ai-trader/` for off-server copies |
| `PAPER_INITIAL_BALANCE_USD` | no | default `50` |
| `KILL_SWITCH_FILE` | no | default `./data/KILL_SWITCH` |
| `LOG_LEVEL` | no | default `INFO` |

## 1. Start

### macOS 12 / no Docker (`make paper-local`)

```bash
uv sync                 # once
make paper-local        # alembic upgrade head, then scheduler + Telegram + dashboard
```

* Dashboard: http://127.0.0.1:8000 (bound to localhost only).
* Stop with `Ctrl-C`. Paper positions are in memory and are lost on stop; on the
  next start, any trade rows left open are closed as `paper_state_lost_on_restart`.
* Smoke test (one brain cycle + one monitor tick, then exit): `make paper-once`.
* To keep it running while the terminal is closed: `nohup make paper-local > logs/paper.log 2>&1 &`
  (`mkdir -p logs` first) — or run it inside `tmux`.

### VPS with Docker

```bash
docker compose build && docker compose up -d     # or: make docker-build docker-up
docker compose logs -f --tail=200                # make docker-logs
ssh -L 8000:127.0.0.1:8000 <vps>                 # dashboard over SSH tunnel
```

The compose file forces `TRADING_MODE=paper` and publishes port 8000 on
`127.0.0.1` only. No Caddy: the TradingView webhook is disabled (ADR-004 (a)).

## 2. Stop / kill switch

| Way | Effect |
|---|---|
| Telegram `/stop` | kill switch ON + market-close all paper positions |
| Dashboard button / `curl -X POST -H 'X-Trader-Action: kill-switch' http://127.0.0.1:8000/api/kill-switch` | same |
| `make kill` (creates `data/KILL_SWITCH`) | position monitor closes everything on its next tick (≤ 30 s) |
| `/pause` | no new entries; open positions keep SL/TP and are still monitored |

The kill switch is checked before every order and on every position-monitor
tick. If its state cannot be read, it counts as **ON** (fail-closed).

**Resume:** `/resume` clears only the pause. `/unhalt` clears the DB kill-switch
flag, but is refused after a failed close or a reconciliation mismatch — verify
positions on the host and run `make unhalt` there. The local file flag is never
cleared remotely: remove it with `make unkill`.

The system also engages the kill switch itself on: SL placement failure,
reconciliation mismatch (broker positions vs DSOR open trades), and the
price-shock emergency path.

## 3. Recover

1. `/status` (or `GET /api/health`) — mode, kill switch, pause, equity, LLM cost.
2. Read why it halted: `sqlite3 trader.db "select ts,type,details from risk_events order by id desc limit 20"`.
3. Fix the cause. Restart if needed (`make paper-local` / `docker compose restart`).
4. `make unkill` if the file flag is set; `/unhalt` (or `make unhalt` on the host); `/resume` if paused.

## 4. Dead-man alert (external)

1. Create a check at an uptime service (e.g. healthchecks.io), period 1 min, grace 3 min,
   with Telegram/email notification to the owner.
2. Put its ping URL in `HEALTHCHECK_PING_URL`.
3. **Test:** stop the app (`Ctrl-C` / `docker compose stop`). Within the grace
   period the service must alert you. Start again; the check goes green.

The ping URL is never logged (it contains a secret id).

## 5. Telegram commands (UTC + PKT on every alert)

`/status /positions /pnl /stop /pause /resume /unhalt /mode` (show) `/mode paper`
(downgrade only — anything else is refused) `/lessons /approve L-0007
/reject L-0007 /confirm <id>` (Phase 11; currently never acts).

Alerts: decision taken (with thesis), order filled, SL/TP hit, Risk Gate
rejection, data outage, job errors, kill switch, daily summary at 19:00 UTC
(00:00 PKT).

## 6. Rotate keys

1. Create the new key at the provider (Anthropic / Telegram BotFather `/revoke` /
   healthcheck "regenerate URL").
2. Edit `.env` on the host by hand.
3. Restart (`docker compose up -d --force-recreate` or restart `make paper-local`).
4. Revoke the old key at the provider.

Exchange API keys are not used in paper mode — leave them empty.

**Note:** `NEWS_API_KEY`, `DERIVATIVES_DATA_API_KEY`, `CROSS_ASSET_DATA_API_KEY`,
`TRADINGVIEW_WEBHOOK_SECRET` and `HEALTHCHECK_PING_URL` values were committed to
`.env.example` in git history (first in `0509e6c` / `3ee8a6c`). Treat them as
leaked: rotate all five.

## 7. Rotate TradingView webhook secret

The webhook is disabled (`feature_flags.tradingview_webhook: false`). If it is
ever enabled: generate a new secret (`python -c "import secrets;print(secrets.token_urlsafe(32))"`),
put it in `.env` as `TRADINGVIEW_WEBHOOK_SECRET`, update the secret inside every
TradingView alert message body, restart.

## 8. Backups and restore

* Daily at 00:30 UTC the app writes `BACKUP_DIR/trader-YYYYMMDD-HHMMSS.db`
  (SQLite online backup, consistent while running), keeps the newest 14, and
  rsyncs to `BACKUP_RSYNC_TARGET` if set. One-off: `make backup`.
* KSOR (`ksor/`) is git-versioned; lesson writes are committed. Push the repo to
  the private remote regularly (`git push origin master`).

**Restore:**
```bash
# stop the app first
cp backups/trader-YYYYMMDD-HHMMSS.db trader.db       # Docker: data/trader.db
make migrate                                          # brings schema to head
make paper-local                                      # or docker compose up -d
```
