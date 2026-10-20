# Read-only Quick Trade validation

From the backend directory, with the existing environment:

```sh
.venv/bin/python -m scripts.validate_quick_trade --current
.venv/bin/python -m scripts.validate_quick_trade --snapshots data/runtime.log --limit 5
.venv/bin/python -m scripts.validate_quick_trade --snapshots /path/to/snapshot.json
```

`--current` only GETs the existing `/api/market/XAUUSD/multi-timeframe` endpoint,
which uses AnalysisService and TwelveDataProvider. It does not start the application.
Use `--market-url` if that endpoint is served at another address. If no server is
available, the result is `data_unavailable`; this command never starts one.

Snapshots accept full `StrategyInput` JSON, full `MultiTimeframeAnalysis` JSON,
JSON arrays/JSONL, or the existing `Agent market context` runtime log records.
Reduced decision summaries cannot replace full analysis and return `invalid_input`.
Log replay uses its UTC log timestamp; standalone analysis uses its quote timestamp
unless the snapshot supplies `evaluated_at`. The limit selects the last N records
per file. Historical replay does not establish that a setup is actionable now.

The report preserves the strategy's setup, measured supporting/conflicting signals,
rejection reasons, invalidation condition and eligible proposal. It also includes
raw selected-timeframe observations, reference price and market timestamp. A
naive candle timestamp is normalized only with known Twelve Data UTC provenance.
Unknown-source naive timestamps stay missing; no price level or cost is invented. Twelve Data's current quote timestamp is assigned on receipt by the
existing provider; it is not an exchange execution timestamp.

`outcome` distinguishes `eligible`, `rejected`, `incomplete_input`, `invalid_input`
and `data_unavailable`. `missing_inputs` is independent of the strategy's early
returns, so missing execution evidence is reported even when market conditions
already fail. The nested evaluation retains the original strategy result; a
missing-input rejection does not prove that the market setup itself is unsuitable.
Subsequent setup checks may not have run after an early input failure.

Analysis alone does not contain execution evidence. Optionally supply genuine
`ExecutionEvidence` JSON with `--execution-evidence /path/to/evidence.json`:
`observed_at`, `source`, `spread`, `additional_round_trip_cost`,
`min_stop_distance`, `min_target_distance`. Costs/distances are XAUUSD price units;
zero must be a known observation, not a substitute for unknown data. Original
observation times are checked against evaluation time, including during replay.
No broker connection is opened by this command. Alternatively pass a captured
`BrokerEvidenceSnapshot` JSON using `--broker-snapshot`; its bid/ask are converted
to spread with Decimal arithmetic. Choose one evidence format, not both.

Eligible validation reports receive a UUID opportunity ID and current UTC detection
time. Historical `evaluated_at` and market time remain separate from detection
time. Strategy evaluation milliseconds use `perf_counter_ns`, excluding fetching
and parsing. Future AI reasoning start/completion, broker submission and fill
confirmation timestamps remain null. These reports are observations, not execution
requests or risk approvals. The deterministic strategy itself is unchanged.

The command prints JSON to stdout and writes no database records. It imports no
application startup, scheduler, agent, or broker code. No integration into the
trading cycle is enabled by this workflow.


## Input sources and normalization

| Input | Existing source | Adapter behavior |
|---|---|---|
| Quote price/time | TwelveDataProvider → MarketQuote → AnalysisService → MultiTimeframeAnalysis | Keep price/time; convert aware offsets to UTC. Provider currently records receipt time because `/price` supplies no exchange timestamp. |
| Candle time | Twelve Data `time_series`, requested with `timezone=UTC` | Provider now attaches UTC to naive source times and converts aware offsets. Historical logs from this provider receive the same normalization during replay. |
| Bid/ask/time | cTrader `ProtoOASpotEvent` or matching-timestamp newest BID/ASK historical ticks | Use original broker milliseconds, scale prices by 100000, subtract ask−bid using Decimal. Never use application receipt time as quote time. |
| SL/TP minima | Existing `CTraderBrokerProvider.get_symbol_metadata("XAUUSD")` | Normalize POINTS using actual symbol digits. Missing fields and percentage-based constraints remain unknown; explicit zero remains zero. |
| Additional trading cost | No existing verified commission/slippage estimate in price units | Remains `None`. Symbol commission metadata alone is insufficient for complete round-trip cost. |

For standalone captured JSON, use `--market-source twelve_data` (or envelope
`"market_source": "twelve_data"`) only when that provenance is known. Runtime logs
from the existing analysis service are tagged automatically. Aware timestamps are
always normalized by conversion, never relabeled. Unknown naive quote timestamps
are never repaired by substituting the evaluation clock.

`input_failure_reasons` reports invalid/missing/stale evidence separately from
`strategy_rejection_reasons`. For example, observed `rejection="none"` without a
confirmed breakout is a real absence of setup, even when execution costs are also
unknown. `outcome` remains `incomplete_input` until required evidence is complete.
`execution_observations` and `broker_observations` expose known values and unknowns.

## Reusing services in a read-only worker

The adapter can be called with existing service instances; it never constructs a
broker connection, authenticates, changes runtime controls or submits an order:

```python
from app.strategies.input_adapter import input_from_services
from app.strategies.validation import validate_snapshot

data, broker_snapshot = input_from_services(
    analysis_service, broker_provider=existing_demo_provider,
)
report = validate_snapshot(data, source="existing-services")
```

Run this synchronous I/O on a worker thread, not the Twisted reactor. Supply a
complete decoded `spot_event` when one is already available. Otherwise the adapter
issues two read-only `ProtoOAGetTickDataReq` requests on the existing connection,
for the previous 60 seconds. Only newest ticks with identical broker timestamps
are paired; nonmatching timestamps return an explicit unavailable-quote diagnostic.
This conservative fallback does not create a subscription or approximate a spread.
It uses [official tick timestamp semantics](https://help.ctrader.com/open-api/messages/#protooagettickdatares)
and [symbol metadata units](https://help.ctrader.com/open-api/model-messages/#protooasymbol).

Omit the broker provider when disconnected. This still evaluates observed market
setups and reports missing execution inputs. A supplied invalid/disconnected/live
provider fails closed. Broker errors surface to the caller; no automatic retries,
recovery, connection starts or zero-cost fallbacks occur. Complete eligibility
still requires verified additional costs and contemporaneous broker evidence.

## Controlled live read-only command

The former `--current` endpoint is
`http://127.0.0.1:8000/api/market/XAUUSD/multi-timeframe`. Connection refused means
there is no HTTP listener at that address. This was verified with the local port
inventory. The endpoint is registered by `app.main:app` and needs a running ASGI
server and `TWELVE_DATA_API_KEY`. **Do not start it for this validation:** its
lifespan also starts the existing scheduler and, in cTrader mode, the trading
runtime. No startup wiring was changed.

Instead, from the backend directory:

```sh
.venv/bin/python -m scripts.validate_quick_trade_live \
  --env-file .env \
  --slippage-allowance 0.10 > /tmp/quick-trade-live-result.json
```

`0.10` is an explicit experimental **round-trip** allowance in XAUUSD price units,
not realized slippage or a default cost. Choose the allowance intentionally. It is
required, finite and nonnegative; omitting it is an error. This command does not
change account risk, trade size, or strategy configuration. It uses the existing
`TRADE_VOLUME_LOTS` solely for commission estimation.

The command constructs the same AnalysisService dependencies as the market route,
using TwelveDataProvider directly. It imports neither `app.main` nor the agent or
scheduler. Required environment keys: `TWELVE_DATA_API_KEY`, `CTRADER_CLIENT_ID`,
`CTRADER_CLIENT_SECRET`, `CTRADER_ACCESS_TOKEN`, `CTRADER_ACCOUNT_ID`, and
`CTRADER_ENVIRONMENT=demo`. CTraderClient performs its existing token/account/DEMO
verification. Existing `DATABASE_URL` configuration is also required by the broker
module imports; no database connection or query is performed by this command.
A single temporary connection is started, and `stop()` runs in
`finally`, including on authentication or request failure. The process does not
stop or restart another process's Twisted reactor or runtime.

Only symbol-list, symbol-detail, historical-tick and asset-list requests are
allowed after authentication. Both order and close guards are false. Execution
events have a no-op handler in this dedicated process, so unsolicited events do
not write to PostgreSQL or invoke protection/position management. No database
migration, recovery, liquidation, SL/TP amendment, or order submission occurs.

Broker `preciseTradingCommissionRate` and `preciseMinCommission` are used with
[official commission units](https://help.ctrader.com/open-api/model-messages/#protooasymbol).
The quote asset is verified as USD through broker symbol and asset responses.
Rates are converted for the existing lot size and configured volume. The estimate
uses two charged sides at the analysis reference price, applying a supported
minimum fee per side. The actual exit price and realized charges are unknown.
Missing precise rates/minima, unsupported currencies, or unknown fee units remain
unknown, never zero. The separate allowance is added only when commission is known.
`costs.realized_slippage` remains null; no fill occurred.

`latency_ms.market_data` measures service construction and current analysis fetch;
`broker_evidence` includes DEMO authentication and evidence requests;
`strategy_evaluation` measures only deterministic strategy execution;
`total` includes setup, adaptation, reporting preparation and disconnection, but
excludes printing JSON. All use monotonic clocks. Source observation times are
preserved; UTC evaluation time is recorded separately after retrieval.

The output can be replayed using `scripts.validate_quick_trade --snapshots` because
it retains full market analysis, normalized broker observations, evaluated_at and
source provenance. It contains no credentials. Connectivity errors identify the
failed stage; full HTTP exception URLs are excluded to avoid disclosing API keys.

### Observed run: 2026-10-19

The successful controlled result is saved in
`docs/quick_trade_live_validation_2026-10-19.json`.

- DEMO account 49167599 verified; connection stopped successfully; no orders.
- Twelve Data reference 4142.84531 at 19:55:25.852086 UTC (provider receipt time).
- Broker quote at 19:55:29.542 UTC: bid 4142.14, ask 4142.27, spread 0.13.
- XAUUSD symbol 41: digits 2, lotSize 10000 protocol cents = 100 units/lot.
  Explicit slDistance/tpDistance 0 in POINTS mode, normalized minima 0.00.
- Precise commission rate 2500000000 / 1e8 = $25 per million USD traded;
  precise minimum commission explicitly 0. At the reference price, estimated
  round-trip commission is 0.2071422655 price units. With the configured allowance
  0.10, additional cost is 0.3071422655; spread is separate.
- Complete inputs; rejected bearish breakout: displacement_detected=false,
  displacement_direction=none, measured strength 0.807. Overall multi-timeframe
  bias neutral; selected 5-minute regime breakout_bearish. ATR 1.9305, ATR ratio
  0.86, broken support 4143.53195. No actionable SL/TP proposal.
- Measured durations: market 1212.900208 ms, broker 9940.052166 ms,
  strategy 0.271 ms, total 11193.233042 ms.

This confirms one read-only evaluation works. It does not establish profitability,
validate realized costs, or authorize autonomous integration. The historical-tick
fallback still requires matching bid/ask timestamps; otherwise execution evidence
remains explicitly incomplete. Quote freshness from Twelve Data is receipt-based,
not an exchange-trade timestamp.
