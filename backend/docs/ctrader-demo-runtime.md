# Autonomous cTrader DEMO runtime

This stage is local-only. Use one Uvicorn worker without reload. `main.py` defaults
to paper mode unless `TRADING_BROKER=ctrader_demo` is explicit. No live environment
is accepted by `CTraderClient`. The runtime verifies token, trading scope, exact
account ID and an explicitly false broker `isLive` field before authentication
is considered complete. No automatic order retries are implemented.

## Database preparation

From the backend directory, keep your existing `.env` credentials private:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot/backend
.venv/bin/python -m scripts.migrate_database --env-file .env --apply
.venv/bin/python -m scripts.migrate_database --env-file .env
```

Stop any owning TradePilot runtime before migrating. The command acquires the same
PostgreSQL advisory lock as the runtime, uses one transaction and a five-second
DDL lock timeout, and verifies column types/nullability/primary keys. It creates
only `broker_position_closes` and `broker_runtime_state` when absent. It never drops,
recreates, truncates or updates existing trading data. Repeating it is safe; an
incompatible pre-existing schema causes an error rather than replacement. Use your
normal PostgreSQL backup procedure before schema changes. No migration runs
implicitly on application startup; startup verifies the required schema.

## Read-only manual broker verification

Run this with the serving runtime stopped (both acquire the same owner lock):

```sh
.venv/bin/python -m scripts.verify_ctrader_runtime --env-file .env
```

It authenticates, verifies the configured DEMO account, reconciles persisted
executions, and reports positions, protection and pending broker orders. It may
update reconciliation records, but **cannot submit opening orders** and does not
invoke maximum-holding-time closes. Check `account_id` against the cTrader DEMO
application and require `demo_verified: true`. Any missing/unconfirmed protection
or unresolved orders require operator review. This procedure was not executed as
part of implementation; automated tests use mocked broker messages.

## Start locally

The existing `.env` must contain DATABASE_URL, CTRADER_CLIENT_ID,
CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN, CTRADER_ACCOUNT_ID and the existing
market-data/reasoning configuration. Use a token with trading permission for the
intended DEMO account only. Do not change volume or risk settings.

```sh
export TRADING_BROKER=ctrader_demo
export CTRADER_ENVIRONMENT=demo
export TRADING_INTERVAL_MINUTES=5
export RUNTIME_CONTROL_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
```

Keep the control token available in the terminal used for the commands below.
Do not publish this API: existing decision/paper endpoints are intended for local
use and do not constitute a production authentication boundary.

The first runtime-state row starts **paused**. No opening order is submitted until
an operator resumes. Pause/critical state persists in PostgreSQL across restarts.
A previously resumed account may resume automatically after restart/reconnect,
but only after fresh authentication, recovery and protection verification. If you
want a maintenance restart to stay paused, call pause before stopping.

A PostgreSQL session advisory lock prevents a second serving runtime from creating
a cTrader connection/scheduler against this database. There is also a process
lifespan guard. Do not run multiple processes against different databases for the
same broker account; their locks and execution records would not be shared.

## Inspect, pause and resume

```sh
curl -fsS -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN" \
  http://127.0.0.1:8000/api/runtime/status

curl -fsS -X POST -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN" \
  http://127.0.0.1:8000/api/runtime/pause

# Explicitly enables autonomous DEMO entries if all current safety checks pass:
curl -fsS -X POST -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN" \
  http://127.0.0.1:8000/api/runtime/resume
```

`new_orders_enabled` describes the runtime gate; the existing market/session,
stale-quote, risk, one-position and unresolved-execution guards still apply.
Status includes account ID, verified DEMO/authentication state, block reason,
persistent critical error, last recovery error and latest position reports.

Pause blocks **new entries**, not observation/recovery. Already-protected positions
can still receive their planned maximum-holding-time close on a scheduler tick.
Broker SL/TP remains active independently of this application. Critical unsafe
states block application closes too; there is no automatic SL/TP repair or emergency
liquidation. Resume rechecks broker state before acknowledging the safety latch.
A current unsafe position cannot be bypassed by resume.

## Runtime and data behavior

- SDK reconnects the existing connection and repeats verified authentication.
  Entry permission is invalidated immediately on disconnect/token invalidation.
- Recovery, management and manual/scheduled trading cycles are serialized. A final
  gate is checked on the reactor immediately before a new-order/close message.
- Unknown pending broker orders, missing metadata, unsafe protection and unsupported
  exposure fail closed. Prepared/uncertain local execution records remain blocked.
- The scheduler runs every five minutes by default. It manages positions even when
  the entry market-session check is closed. The recovery monitor checks state about
  every ten seconds. Neither loop retries an uncertain order.
- Existing agent decisions and market context are persisted in PostgreSQL. Execution
  requests/results and close attempts use the existing persistence services.
- Timestamped cycle, approval/block, request/result, broker-state and error logs are
  written to `data/runtime.log` with rotation (10 MB, five backups). Set
  `TRADING_LOG_FILE` to choose a different local path. Credentials/tokens are not logged.
- Ctrl-C disables submission gates first, drains scheduler work, stops the monitor
  and SDK reconnect service, and releases database ownership. It does not close
  positions. The process-wide Twisted reactor is never stopped/restarted in-process.

## Paper fallback

```sh
TRADING_BROKER=paper TRADING_INTERVAL_MINUTES=5 \
  .venv/bin/uvicorn app.main:app --env-file .env --host 127.0.0.1 --port 8000 --workers 1
```

Paper routes always use the paper manager, including when cTrader mode is active.
The fallback is explicit; broker authentication failure never silently switches modes.

## Before unattended operation

Complete the read-only broker check, then a supervised DEMO session with an explicit
operator resume. Validate real SL/TP distances, a time-limit close, reconnect recovery,
API quotas, market-data freshness and reasoning-provider availability. These checks
were not performed by automated tests. Keep credentials refreshed; token renewal
is an operator task. Establish external alerting for critical logs and a response
policy for unprotected positions. Do not leave the runtime unattended until those
checks pass. Partial/uncertain close outcomes remain blocked for manual review.
Runtime ownership/safety state assumes one account and one shared PostgreSQL database.
