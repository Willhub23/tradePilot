# Quick Trade research strategy

This package evaluates supplied snapshots only. It is not selected or invoked by
the trading cycle, FastAPI, scheduler or broker. Evaluation has no network, DB,
wall-clock, LLM or order-submission dependency. Its inputs and research result stay
separate from execution requests and account-level risk approval.

- `base.py`: minimal `Strategy.evaluate(input)` protocol.
- `models.py`: existing snapshot/evidence/proposal/result models, with additive
  setup, decision-explanation and invalidation diagnostics.
- `quick_trade_config.py`: immutable experimental thresholds.
- `quick_trade.py`: setup detection plus the existing common viability filters.

## Actual supplied inputs

`StrategyInput` still takes `MultiTimeframeAnalysis`, explicit `evaluated_at` and
optional `ExecutionEvidence`. The default selected timeframe is `5min`; `15min` is
configurable. The strategy reads the selected timeframe's support/resistance,
ATR, volatility context, trend/structure labels, EMA values/slope labels, and
`price_action.rejection` / displacement fields. These are the existing analysis
service's outputs; indicators are not recalculated or invented.

A latest-candle rejection label describes candle shape. Combined with current
price near a measured boundary, it is evidence for a reaction candidate, not proof
of an entire historical sequence or future bounce. EMA slopes are categorical;
the strategy does not invent numerical slope magnitudes. A single snapshot cannot
prove an ordered breakout-retest sequence, so no `retest` setup is claimed.

## Setup-specific entry rules

1. **Range support bounce / resistance rejection:** a supplied bullish/bearish
   rejection label, price on the holding side of the corresponding measured
   support/resistance, within `max_structure_distance_atr` (default one ATR).
   Price must remain inside the supplied selected-timeframe bounds when present.
   This level-reaction setup can occur in ranging or trending markets. Mixed
   high/low patterns and conflicting EMAs are reported, not universal vetoes.
   A directly opposing observed displacement invalidates the reaction.
2. **Trend continuation after pullback:** the same structural reaction plus supplied
   trend direction **or** structure matching the reaction, price within the
   configured EMA20 pullback zone, and a non-ranging regime. Full HH/HL or LL/LH,
   EMA9/20/50 ordering and matching EMA20/50 slopes are not all required. A reaction
   can fall back to the range category if the pullback classification is unsupported.
3. **Breakout:** a supplied `break_above`/`break_below` marker, matching detected
   displacement with finite positive measured strength, and quote beyond the
   corresponding measured broken resistance/support. Entry must be within
   `max_breakout_distance_atr` (default one ATR) of that level. Counter-direction
   rejection or failure to hold the broken level invalidates the setup. EMA
   disagreement alone does not invalidate this directly observed break.

An explicit break marker is evaluated first; an unconfirmed/failed break is not
silently relabeled as a bounce. Otherwise, classify an observed structural reaction
as a pullback when its conditions are met, or as a range reaction. Aligned indicators
without a measured structural reaction or confirmed break do not create an entry.

All setups need a measured opposing level to establish target room. The strategy
selects the nearest appropriate supplied resistance above a long / support below
a short across the available timeframes. It never skips a closer obstacle to justify
a larger target. Additional timeframes' levels require aware, nonfuture timestamps
within `max_opposing_level_age_seconds` (default 3600). A broken latest swing is an
anchor, not a replacement target: without another observed opposing level, a
breakout is rejected rather than extrapolating structure from ATR.

## Common levels and viability filters

For the chosen direction and measured anchor:

```
buffer          = 0.1 * ATR
stop distance   = max(distance to anchor + buffer, 1.0 * ATR)
target distance = min(room to nearest opposing level - buffer, 2.0 * ATR)
```

The original configurable bounds remain validation limits, not clamps: SL
$1.50–$5.00, target movement $2.00–$5.00, maximum holding time 30 minutes.
Decimal calculations preserve distance/boundary precision. Broker tick/relative
order quantization remains the existing integration's responsibility; this strategy
does not submit or round broker orders. Dollars describe XAUUSD price movement,
not account P&L.

All setups retain freshness checks, ATR 0.75–3.00, rejection of high volatility or
supplied ATR ratio >1.75, spread <=0.50, total costs <=20% of target, and net
`(target - costs) / (stop + costs) >= 1.2`, with the existing configuration overrides.
These research thresholds do not change application risk/volume policies.

Execution observations must provide a timestamp/source, spread, additional
round-trip commission/slippage estimate excluding spread, and broker minimum SL/TP
distances already normalized into XAUUSD price units upstream. Missing evidence is
not zero cost. Explicitly measured zero is valid. Levels are never moved to satisfy
broker minima. Invalid essential prices/ATR/levels reject; unavailable indicators
are recorded as conflicts when a structural setup can be evaluated without them.

## Result and integration boundary

The original strategy ID, proposal fields, quote/candle/evaluation timestamps,
evidence lists and rejection diagnostics remain. This checkout has no existing
opportunity-ID or latency fields in the strategy; none are removed or fabricated.

Every result adds `setup_type`, `decision_reason`, `invalidation_condition` and
`invalidation_level` when a setup/anchor is identified. Invalidation explicitly
states loss of the measured reaction/broken level. Eligible results contain the
candidate entry, SL/TP, holding limit, estimated cost and net reward/risk. Rejected
results contain no actionable proposal; measurable candidate levels remain in
diagnostic evidence when derivable. Entry and exit references' source timeframes
are included. A research candidate is not an execution approval.

Trading-cycle integration, runtime activation, new persistence, and retest detection
from ordered candle history are outside this revision. Future integration must
retain current risk approval, broker precision checks, DEMO-only protection,
one-position/unresolved-order guards and existing operational instrumentation.
