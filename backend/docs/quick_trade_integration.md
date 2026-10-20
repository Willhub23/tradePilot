# Opt-in Quick Trade DEMO integration

Quick Trade is **not activated** by this change. `TRADING_STRATEGY=existing` is the
unchanged default, and no environment file, running process or production database
was modified during implementation/testing. Quick Trade entry rules are unchanged.

## Selection and execution

- `TRADING_STRATEGY=existing|quick_trade` selects the existing flow or Quick Trade.
- Quick Trade requires `TRADING_BROKER=ctrader_demo`, the existing authenticated,
  recovered DEMO runtime, and durable execution persistence. Paper mode cannot
  execute this strategy.
- `QUICK_TRADE_SLIPPAGE_ALLOWANCE` is required when selecting Quick Trade: a finite,
  nonnegative **round-trip XAUUSD price-distance allowance**, not realized slippage.
- `QUICK_TRADE_MAX_PRICE_DRIFT` defaults to 0.25 price units.
- `QUICK_TRADE_MAX_OPPORTUNITY_AGE_SECONDS` defaults to 60; values in (0, 60] are
  accepted. This expiry is an execution guard, not a changed strategy entry rule.

The existing scheduler and persistent cTrader connection are reused. Each entry
cycle first checks pause/connection/recovery/safety, positions and unresolved entry
or close executions. An existing position or unresolved execution skips new-entry
AI analysis. The existing monitor and scheduler position-management phase continue
checking recovery, protection and holding time. The same exposure skip applies to
the existing DEMO strategy; paper behavior is preserved.

Quick Trade then reuses AnalysisService/TwelveDataProvider and the existing
read-only broker collector and precise commission normalizer. Unknown costs,
unsupported metadata, missing contemporaneous bid/ask or stale data block AI and
submission. Rejections and incomplete inputs are recorded without Ollama inference.

Eligible proposals and measured supporting/conflicting evidence enter the existing
AgentService/LocalReasoningProvider. The conditional Quick Trade prompt asks the AI
to approve that proposal's direction as intraday or HOLD. Opposite directions or
swing substitutions become HOLD before agent persistence. The existing default
prompt and default market-context payload remain unchanged. Macro blocking,
decision validation and grounding validation still apply.

RiskService remains unchanged. Quick Trade execution requests preserve the original
proposal's absolute SL/TP and maximum holding time (up to 30 minutes). They bypass
only the generic intraday/swing ATR plan derivation. Existing trade volume, broker
precision rounding, broker-side protection verification and holding-time management
remain in force. Maximum holding time is enforced by the existing position manager
at its configured cadence, not a new dedicated timer.

After AI, market analysis, broker quotes, costs, structure validity and macro context
are refreshed. The proposal must retain its setup, direction and structural anchor.
Execution is rejected for changed setup/insufficient target room, excessive drift,
expiry, unsafe costs, stale quotes, or increased original stop-risk distance.
Original SL/TP are never silently replaced by a fresh proposal. Inside the runtime
entry lock, exposure, unresolved orders, risk, session and safety epoch/generation
are checked again. The provider rechecks a current broker quote after its existing
recovery/protection checks and validates the actual rounded wire distances before
sending. A broker fill can still slip; existing relative SL/TP and post-fill
verification semantics are preserved, without local stops or automatic amendments.

A separate cycle lock prevents simultaneous manual/scheduled cycles; it is not the
runtime lock. Runtime locks are **not held across inference**, including in the
existing DEMO path. Pause closes the reactor-side order gate immediately and
invalidates proposals spanning a pause/resume, even if inference later completes.
No client is constructed/authenticated/disconnected per Quick Trade evaluation.

## Persistence and timing

Apply additive `003_strategy_evaluations.sql` using the existing migration command.
It creates `strategy_evaluations` without dropping or altering existing tables.
The migration transaction uses the existing runtime ownership/advisory lock and
verifies compatibility. Existing strategy runtime startup does not require this new
table; selecting Quick Trade does. The explicit migration verification command
checks all migration tables.

One strategy-evaluation row is created per cycle and progresses through its outcome.
Its JSON includes market/quote evidence, costs and allowance, full strategy report,
setup, rejection/block reason, original reference and source timestamps,
pre-submission revalidation, UTC lifecycle times and measured durations. Rejected
cycles have no invented AI decision ID or opportunity ID. `AgentResult.decision_id`
is null when no AI/agent decision was created; `strategy_evaluation_id` identifies
the journal row instead. Eligible reports retain their opportunity UUID throughout
revalidation.

Links are:

```text
strategy_evaluations.id / opportunity_id
  -> agent_decision_id -> agent_decisions.id (proposal/evidence also in market_context)
  -> request_id -> broker_executions.request_id
       -> request_data.opportunity_id / strategy_id / unchanged SL/TP / hold limit
       -> broker_order_id / broker_position_id / cumulative fill data
```

The evaluation row retains the cycle's immediate result; join its request_id to
broker_executions for the latest asynchronous/reconciled status. No dashboard or
Decision Journal endpoint is added. `StrategyEvaluationPersistenceService.get_recent`
provides a backend read path for later journal work.

Timing coverage:

- `detection_ms`: market retrieval, broker evidence, adaptation and strategy stage;
  `strategy_evaluation_ms`: deterministic evaluation alone.
- Agent-stage UTC start/end and duration include validation/persistence. Nested
  `ai_reasoning` times only the actual reasoning-provider call; absent if macro
  context forced HOLD without inference.
- Initial and final risk-validation durations are monotonic and UTC-tagged.
- `order_submission_ms` measures durable submission intent through provider return;
  `broker_call_ms` measures the provider call alone (including existing safety and
  post-fill protection checks). UTC submission start is in request_data.timing.
- `request_data.execution_tracking` stores broker fill event UTC and its source
  (`deal.executionTimestamp` or `order.utcLastUpdateTimestamp`) separately from
  callback receipt UTC. It records submission-to-fill-confirmation milliseconds
  only when both monotonic readings belong to the same process.
- Acceptance is not a fill. No fill time or realized slippage is fabricated.
  Duplicate/late events retain terminal-status protections and earliest observed
  fill receipt. Recovered outcomes after restart cannot establish historical local
  monotonic latency, which remains unknown.

## Supervised operator procedure — do not run unattended

These are operator steps; implementation/testing did **not** run them or place
DEMO orders. Use the existing backend virtual environment and PostgreSQL database.
Do not use multiple workers or `--reload`.

1. Pause and stop any existing serving runtime before migration. If currently
   serving, use its authenticated pause endpoint first. The migration and offline
   pause below refuse an active database owner; do not bypass that lock.

2. From the backend directory, apply and verify the additive migration:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot/backend
.venv/bin/python -m scripts.migrate_database --env-file .env --apply
.venv/bin/python -m scripts.migrate_database --env-file .env
```

3. Ensure the durable account state is paused **before** starting the selected
   strategy, preserving any critical latch. This offline command starts no broker
   connection and refuses an active runtime owner:

```sh
.venv/bin/python - <<'PY'
import os
from dotenv import load_dotenv
load_dotenv('.env')
from app.services.broker_runtime_store import BrokerRuntimeStore
store = BrokerRuntimeStore(os.environ['CTRADER_ACCOUNT_ID'])
store.acquire()
try:
    state = store.load()
    store.save(True, state['critical_error'])
finally:
    store.release()
PY
```

4. Explicitly select Quick Trade and start one DEMO serving process, still paused:

```sh
CTRADER_ENVIRONMENT=demo TRADING_BROKER=ctrader_demo \
TRADING_STRATEGY=quick_trade QUICK_TRADE_SLIPPAGE_ALLOWANCE=0.10 \
QUICK_TRADE_MAX_PRICE_DRIFT=0.25 QUICK_TRADE_MAX_OPPORTUNITY_AGE_SECONDS=60 \
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

5. In another terminal, load the existing control token into a shell variable
   without printing it, then inspect status:

```sh
cd /Users/admin/Documents/Repos.nosync/tradePilot/backend
RUNTIME_CONTROL_TOKEN="$(.venv/bin/python -c "from dotenv import load_dotenv; import os; load_dotenv('.env'); print(os.environ['RUNTIME_CONTROL_TOKEN'])")"
curl --fail -sS http://127.0.0.1:8000/api/runtime/status \
  -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN"
```

Confirm the expected account_id, authenticated=true, demo_verified=true,
paused=true, new_orders_enabled=false, and no unresolved critical/recovery issue.
Do not clear an unsafe-protection latch merely to force a test. While still
paused, GET `/api/agent/decision/XAUUSD` once: Quick Trade should return
`execution_block_reason="trading_paused"`, a strategy_evaluation_id and no AI
inference. This verifies selection before any order is permitted.

6. Only while supervising and intending to permit actual DEMO submissions, resume,
   request one cycle, then pause again. Resume also permits the existing scheduler;
   the shared cycle lock prevents overlapping manual/scheduled entry analysis.
   A rejected/incomplete result should show no agent decision/inference. An eligible
   proposal may produce a real DEMO order after AI/risk/safety approval.

```sh
curl --fail -sS -X POST http://127.0.0.1:8000/api/runtime/resume \
  -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN"
curl --fail -sS --max-time 350 http://127.0.0.1:8000/api/agent/decision/XAUUSD
curl --fail -sS -X POST http://127.0.0.1:8000/api/runtime/pause \
  -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN"
```

Pause can also be issued from a separate terminal while AI is still waiting.
Keep the runtime running to monitor/manage any open position; pausing prevents
new entries but does not disable protection/recovery/holding-time management.
Inspect the linked strategy_evaluations, agent_decisions and broker_executions
records and existing runtime log. Confirm original SL/TP, hold limit, confirmed
broker protection, stable IDs and actual fill-receipt telemetry. To roll back
selection, pause first, stop the process and restart with TRADING_STRATEGY=existing;
the additive journal table can remain in place.

Remaining supervised checks: real Ollama review quality/latency, stale-opportunity
rejection under that latency, asynchronous DEMO fill/protection/holding-time
behavior and operator pause during inference. No real inference, order, migration
or autonomous startup is performed by the automated integration tests.

## Files in this change

New implementation files:

- `app/config/strategy_config.py`
- `app/services/quick_trade_cycle_service.py`
- `app/services/strategy_evaluation_persistence_service.py`
- `app/services/execution_timing.py`
- `app/strategies/execution_validation.py`
- `migrations/003_strategy_evaluations.sql`

Updated integration points:

- `app/services/trading_cycle_service.py`
- `app/services/agent_service.py`
- `app/providers/local_reasoning_provider.py`
- `app/services/agent_decision_persistence_service.py`
- `app/models/agent_models.py`
- `app/models/execution_models.py`
- `app/db_models.py`
- `app/providers/ctrader_broker_provider.py`
- `app/services/ctrader_runtime_service.py`
- `app/integrations/ctrader/client.py`
- `app/services/ctrader_execution_event_service.py`
- `app/services/broker_execution_persistence_service.py`
- `app/strategies/validation_costs.py`
- `scripts/validate_quick_trade_live.py`
- `scripts/migrate_database.py`

Tests and documentation:

- `tests/test_quick_trade_integration.py`
- `tests/test_quick_trade_execution_timing.py`
- `tests/test_ctrader_broker_provider.py` (receipt-metadata assertions)
- `tests/test_ctrader_execution_events.py` (off-reactor dispatch metadata)
- `docs/quick_trade_integration.md`

The existing strategy entry implementation, RiskService, volume configuration,
LocalPaperBrokerProvider, scheduler configuration and application startup wiring
are unchanged.
