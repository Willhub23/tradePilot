# Quick Trade read-only observation

Disabled by default. Enable only in a separate supervised serving session after stopping the previous server cleanly. Never run multiple workers or use reload. This procedure does not activate order submission or invoke Ollama.

From the backend directory, with the existing PostgreSQL, Twelve Data configuration and verified cTrader DEMO credentials available:

```sh
source .venv/bin/activate
export TRADING_BROKER=ctrader_demo
export CTRADER_ENVIRONMENT=demo
export TRADING_STRATEGY=existing
export QUICK_TRADE_OBSERVATION_ENABLED=true
export TRADING_INTERVAL_MINUTES=5
: "${RUNTIME_CONTROL_TOKEN:?Set the existing runtime control token}"
: "${QUICK_TRADE_SLIPPAGE_ALLOWANCE:?Set an explicitly approved price-distance allowance}"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
```

The allowance is a pre-trade estimate in XAUUSD price units, not measured realized slippage. Unknown commission remains missing evidence, never zero. Existing broker metadata, quotes and market analysis are reused over the single persistent DEMO connection. No new migrations are required; startup verifies that the existing strategy table is compatible.

Check runtime status:

```sh
curl --fail -H "Authorization: Bearer $RUNTIME_CONTROL_TOKEN" http://127.0.0.1:8000/api/runtime/status
```

Expect `observation_enabled: true`, `paused: true`, `new_orders_enabled: false`. `observation_block_reason: null` means collection is permitted; the entry block remains `observation_only`. Resume requests return HTTP 409 while this process is observing. Pause continues to persist the normal entry pause; it does not stop observations. Stop the process with Ctrl-C to stop collection.

Both new-order and close-order gates are shut. Position reconciliation, SL/TP verification and recovery continue, but automatic holding-time closes are disabled in this read-only session. Existing broker-side protection remains active. Use an appropriate supervised operational session if an existing position requires application-managed closure; observation is not a replacement for that management.

To return to normal execution later, stop the observer cleanly, set `QUICK_TRADE_OBSERVATION_ENABLED=false`, select the intended `TRADING_STRATEGY`, and restart deliberately. The persisted entry pause remains set even after removing the flag. This task does not authorize resuming trading; a separate explicit operator resume is required.

Each cycle stores a row in `strategy_evaluations`, marked `data.mode=observation`, with eligible/rejected/incomplete/blocked status. Successful collection includes the existing report's setup, supporting/conflicting measurements, rejection codes, ATR/volatility, source timestamps/reference price, broker bid/ask/spread/constraints, cost evidence, source-price comparison, and any proposed entry/SL/TP/holding period. Eligible reports retain their opportunity identifier and UTC detection timestamp. No decision or execution-request link is created. Monotonic detection, strategy-evaluation and total observation durations are stored; AI/submission/fill measurements remain absent.

Read the persisted diagnostics with the existing command:

```sh
python -m scripts.summarize_quick_trade
```

The summary includes all persisted modes; observation eligibility does not mean an AI-approved or executed trade. Filter `data->>'mode' = 'observation'` in read-only SQL when isolating this cohort. Scheduler collection follows existing market-session rules and starts at its next interval. Closed sessions are skipped. Unsafe position-management failures can stop a scheduler tick before an evaluation row is created; existing runtime logs and the persistent critical latch capture those failures. Disconnected/unsafe states reaching the observation path produce blocked rows. No fabricated historical or missing execution evidence is supplied.
