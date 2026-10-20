# Quick Trade price-source consistency and rejection diagnostics

No strategy entry rule, ATR bound, AI prompt, position size or risk threshold was
changed. No runtime/scheduler was started or resumed, no broker order was submitted,
and no migration or production database update was performed for this investigation.

## Data flow and timestamp meaning

`TwelveDataProvider.get_price("XAUUSD")` calls Twelve Data `/price` with `XAU/USD`.
`AnalysisService.analyze_multi_timeframe` copies that quote's price/timestamp into
`MultiTimeframeAnalysis.price/price_timestamp`. It does not copy a candle close or
calculate an average for this top-level reference.

Twelve Data documents `/price` as current or most recently available price, with a
response containing only `price`. TradePilot assigns UTC **local receipt time**
after the response. The 30-second cache returns the same quote and timestamp;
a cache hit does not refresh its age. This measures time since receipt, not age
of the underlying market event. The response used here does not identify bid,
ask, venue, or the underlying price's event timestamp.

Candles come separately from `/time_series`, requested in UTC. The provider
normalizes their original timestamps to UTC. The candle timestamp describes its
bar, not the last update of the close. Candles are cached independently (5-minute
analysis TTL 240 seconds). Structure, price action, indicators, EMA-relative flags
and swing distances use supplied candles and `candles[-1].close`, not the current
`/price` reference. Therefore a candle-based `price_vs_ema_20="below"` can coexist
with a newer top-level quote above that EMA; it does not imply that the current
broker quote is below the EMA. Last-bar completeness/update time is not persisted.
No candle or indicator values were rewritten in this change.

`collect_broker_snapshot` uses account-scoped XAUUSD metadata from the existing
cTrader provider. It pairs the newest BID and ASK historical ticks only when their
broker timestamps match, or accepts a complete timestamped spot event. Protocol
prices are divided by 100000; `digits` determines symbol precision separately.
The observed_at value is the broker tick/spot **event time**, not application
receipt time. Subsequent metadata/commission requests can add several seconds
before evaluation. No new broker connection was opened for this investigation.

Both integrations map nominal gold/USD. The cTrader commission helper additionally
verifies the broker symbol's USD quote asset. This is not proof of identical venue,
contract specifications, liquidity provider, sided-price convention or update
frequency. The saved evaluation does not retain Twelve Data venue/convention
metadata. It cannot establish that Twelve Data's value is a cTrader midpoint.

Sources: [Twelve Data latest-price contract](https://twelvedata.com/docs#price),
[cTrader tick timestamps](https://help.ctrader.com/open-api/messages/#protooagettickdatares),
[cTrader price interpretation](https://help.ctrader.com/open-api/symbol-data/).

## Actual paired observation

Two PostgreSQL rows were read using an explicit repeatable-read, read-only
transaction, and independently summarized at 2026-10-20 09:34:33 UTC. The complete
saved summary is `docs/quick_trade_diagnostics_2026-10-20.json`.

| Measurement | Observed |
|---|---:|
| Twelve Data reference | 4125.75295 |
| cTrader bid / ask | 4124.69 / 4124.79 |
| cTrader midpoint | 4124.74 |
| Reference minus midpoint | +1.01295 |
| Reference minus bid / ask | +1.06295 / +0.96295 |
| Twelve Data receipt timestamp | 2026-10-20T09:17:14.258584Z |
| cTrader event timestamp | 2026-10-20T09:17:14.854000Z |
| Timestamp separation | 0.595416 seconds |
| Evaluation time | 2026-10-20T09:17:20.959913Z |
| Reference receipt / broker event age at evaluation | 6.701329 / 6.105913 seconds |

The $1.01295 midpoint gap is 50.6475% of the current $2 minimum target and
20.259% of the $5 maximum target. **There was no proposed TP** because this setup
was rejected; its gap as a fraction of an actual proposed TP is unknown. There is
one receipt/event-proximate pair (default reporting skew window 5 seconds), and it
exceeds 20% of both configured target bounds. This is one observation, not evidence
of an enduring offset or a representative discrepancy frequency. Receipt/event
proximity does not establish market-event synchronization.

The difference is not explained by the $0.10 broker spread or ordinary 2-decimal
rounding. Its cause cannot be resolved from this snapshot: independent feeds,
conventions, underlying quote delay or movement between observations remain
possible. Do not subtract an empirical constant from structure or protection levels.

## Execution safety and smallest correction

The existing post-AI pipeline already refreshes analysis/quotes, compares executable
ask for LONG or bid for SHORT against the original entry, and rejects drift beyond
`StrategySelection.max_price_drift` (default $0.25). It checks original stop budget,
costs, target room, opportunity expiry and source freshness. The provider repeats
broker quote/rounded-protection checks after runtime protection/recovery checks.
A persistent discrepancy of this size would fail the default final drift guard
if the strategy otherwise qualified. This historical rejection created no request,
so its effective runtime drift configuration was not retained and is not inferred.

Residual limitations: a recently received Twelve Data value may itself be old;
receipt age cannot prove source event freshness. Candle timestamps also do not
prove last-close update time/completeness. Quote checks bound known age/drift but
cannot prove cross-feed market-event alignment or eliminate movement after checks.

The correction reuses the **same existing configured drift limit before AI** for
otherwise eligible Quick Trade proposals. It compares the actual executable
broker side, not midpoint, to the unchanged strategy reference. Known material
incompatibility becomes a blocked outcome with input reason code
`analysis_broker_price_discrepancy`; Ollama is not called. The underlying strategy
result and proposal remain intact, showing that the entry rules were not changed.
Already-rejected setups retain their genuine rejection reasons. After-AI drift
blocking remains in place. Broker execution prices remain authoritative.

Every future cycle also retains price-comparison diagnostics and snapshots of
strategy/guard settings in existing JSON fields. No schema change is required.
Existing rows are not backfilled or rewritten. Prices, structural levels and SL/TP
are never shifted to reconcile feeds. No new trade was enabled during this work;
apply/restart only through the existing supervised operational procedure.

## Actual rejection and latency statistics

| Category | Count |
|---|---:|
| Total journal rows | 2 |
| Eligible | 0 |
| Rejected | 1 |
| Incomplete | 0 |
| Blocked/control skips | 1 (trading_paused) |
| Actual market evaluations | 1 |
| Identified setups | 0 |
| Eligible opportunities reaching Ollama | 0 |
| Execution intents / confirmed fills | 0 / 0 |

The one market evaluation has both `volatility_unsuitable` and
`reaction_evidence_missing`. These are two reasons on one evaluation, not two
rejected opportunities. It observed ATR 3.8779, normal volatility regime and ATR
ratio 0.986. ATR exceeded the current 3.0 cap; the 1.75 ratio limit was not exceeded.
The recorded rejection message also names the 0.75–3 ATR interval. Historical
configuration was not stored; the command explicitly labels current-default
fallback comparisons. The absence of an identified setup is not an automatic
rejection of all ranging markets.

Detection latency was 7086.603334 ms; deterministic evaluation was 0.564334 ms.
Each distribution has n=1, so min/median/p90/max all coincide and imply no trend.
AI, submission and fill latency have no observations and remain null, not zero.
The control skip contributes no market, ATR or price-pair observations.

## Read-only command

From the backend directory:

```sh
.venv/bin/python -m scripts.summarize_quick_trade --env-file .env
.venv/bin/python -m scripts.summarize_quick_trade --env-file .env \
  --since 2026-10-20T00:00:00Z --until 2026-10-21T00:00:00Z \
  --max-time-skew-seconds 5 --meaningful-tp-fraction 0.20
```

The command queries all rows in the requested window without silently truncating.
It joins linked agent/execution records, uses read-only transactions with a
15-second statement timeout, and imports no runtime startup, scheduler, reasoning
or broker services. It does not persist new evaluations or backfill history.
Date filters are inclusive since/exclusive until and require timezone information.

For offline reproduction from a read-only exported snapshot:

```sh
.venv/bin/python -m scripts.summarize_quick_trade \
  --from-json tests/fixtures/quick_trade_persisted_sample.json
```

Outputs distinguish initial gate outcomes from final cycle statuses, and count
reason codes once per cycle. Pre-submission revalidation does not become a second sample.
Prepared execution intent, entry into the agent stage, actual measured reasoning,
broker submission confirmation and full/partial fill evidence are separate.
Agent-stage entry or an agent_decision_id alone does not prove Ollama ran;
uncertain cases are reported as unknown. A prepared/unknown request is not a
confirmed fill. Paused skips count as blocked control records, not market rejection.

Reporting time-skew and meaningful-TP thresholds affect diagnostics only, not
strategy rules or execution policy. No target is reconstructed for rejected setups.
Per-row persisted ATR limits are preferred; historical rows without them are
compared against explicitly labeled current defaults.

## Recommendation and changed files

Ready for **longer supervised read-only price/evidence collection**, not a claim
that longer order-enabled trading is validated. One observed setup cannot justify
ATR/entry changes or profitability conclusions. Keep submissions disabled until
operator review of the new guard and a controlled test confirms its intended
blocking. The existing standalone read-only validation command can capture more
current pairs without runtime activation. Its stdout reports are not automatically
inserted into strategy_evaluations; archive them separately. A paused runtime skips
analysis, so collecting more paused skip rows alone will not improve this sample.

Changed implementation: `app/strategies/price_consistency.py`,
`app/strategies/validation.py`, `app/services/quick_trade_cycle_service.py`,
`app/services/strategy_diagnostics.py`, `scripts/summarize_quick_trade.py`.

Added focused tests/fixture: `tests/test_strategy_diagnostics.py`,
`tests/test_quick_trade_price_consistency.py`,
`tests/fixtures/quick_trade_persisted_sample.json`.

Documentation/results: this file and `docs/quick_trade_diagnostics_2026-10-20.json`.
