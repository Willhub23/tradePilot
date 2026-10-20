"""Deterministic structural reactions, pullbacks and confirmed breakouts."""

import math
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.strategies.models import (
    StrategyEvaluation,
    StrategyInput,
    StrategyRejection,
    StrategySignal,
    StrategyTradeProposal,
)
from app.strategies.quick_trade_config import QuickTradeConfig


def _decimal(value):
    return Decimal(str(value))


def _aware(timestamp):
    return (
        timestamp is not None
        and timestamp.tzinfo is not None
        and timestamp.utcoffset() is not None
    )


@dataclass(frozen=True)
class _Setup:
    kind: str
    direction: str
    anchor: float | None
    valid: bool = True

    @property
    def side(self):
        return 1 if self.direction == "long" else -1

    @property
    def invalidation(self):
        if self.anchor is None:
            return None
        relation = "below" if self.side == 1 else "above"
        role = "broken level" if self.kind == "breakout" else "reaction level"
        return f"Price moving {relation} the measured {role} {self.anchor} invalidates {self.kind}"


class QuickTradeStrategy:
    strategy_id = "quick_trade_v1"

    def __init__(self, config: QuickTradeConfig | None = None):
        self.config = config or QuickTradeConfig()

    def evaluate(self, data: StrategyInput) -> StrategyEvaluation:
        config, market = self.config, data.analysis
        supporting, conflicting, reasons = [], [], []
        timeframe = None
        setup = None

        def reject(code, message, measured=None, limit=None):
            reasons.append(
                StrategyRejection(
                    code=code, message=message, measured=measured, limit=limit
                )
            )

        def evidence(name, values, condition, code, message):
            signal = StrategySignal(name=name, values=values)
            (supporting if condition else conflicting).append(signal)
            if not condition:
                reject(code, message)

        def finish(proposal=None):
            return StrategyEvaluation(
                strategy_id=self.strategy_id,
                symbol=market.symbol if market else None,
                status="rejected" if reasons else "eligible",
                proposal=None if reasons else proposal,
                setup_type=setup.kind if setup else None,
                decision_reason=(
                    "; ".join(reason.message for reason in reasons)
                    if reasons
                    else f"{setup.kind} {setup.direction}: confirmed reaction/break at measured level {setup.anchor}; distances, volatility and execution checks passed"
                ),
                invalidation_level=setup.anchor if setup else None,
                invalidation_condition=setup.invalidation if setup else None,
                supporting_signals=supporting,
                conflicting_signals=conflicting,
                rejection_reasons=reasons,
                timeframe=config.timeframe,
                market_timestamp=(
                    market.price_timestamp
                    if market and _aware(market.price_timestamp)
                    else None
                ),
                candle_timestamp=(
                    timeframe.latest_candle_timestamp
                    if timeframe and _aware(timeframe.latest_candle_timestamp)
                    else None
                ),
                evaluated_at=data.evaluated_at,
            )

        def check_time(timestamp, name, maximum_age):
            if not _aware(timestamp):
                reject(
                    f"{name}_timezone_missing",
                    f"{name} requires a timezone-aware timestamp",
                )
                return
            age = (data.evaluated_at - timestamp).total_seconds()
            if age < 0:
                reject(
                    f"{name}_from_future",
                    f"{name} is later than evaluation time",
                    age,
                    0,
                )
            elif age > maximum_age:
                reject(
                    f"{name}_stale",
                    f"{name} age {age:g}s exceeds {maximum_age}s",
                    age,
                    maximum_age,
                )

        if market is None:
            reject("market_analysis_missing", "A market-analysis snapshot is required")
            return finish()
        if market.symbol != "XAUUSD":
            reject(
                "unsupported_symbol",
                "Quick Trade supports XAUUSD only",
                market.symbol,
                "XAUUSD",
            )
        selected = [tf for tf in market.timeframes if tf.interval == config.timeframe]
        if len(selected) != 1:
            reject(
                "timeframe_missing_or_ambiguous",
                f"Expected exactly one {config.timeframe} analysis",
                len(selected),
                1,
            )
            return finish()
        timeframe = selected[0]
        check_time(market.price_timestamp, "quote", config.max_quote_age_seconds)
        check_time(
            timeframe.latest_candle_timestamp, "candle", config.max_candle_age_seconds
        )
        for name, value in {"price": market.price, "atr": timeframe.atr}.items():
            if value is None or not math.isfinite(value) or value <= 0:
                reject(
                    "required_measurement_invalid",
                    f"{name} must be present, finite and positive",
                    name,
                )
        for name in ("support", "resistance"):
            value = getattr(timeframe, name)
            if value is not None and (not math.isfinite(value) or value <= 0):
                reject(
                    "required_measurement_invalid",
                    f"Supplied {name} must be finite and positive",
                    name,
                )
        if (
            timeframe.support is not None
            and timeframe.resistance is not None
            and timeframe.support >= timeframe.resistance
        ):
            reject(
                "structure_levels_invalid",
                "Measured support must be below measured resistance",
            )
        if timeframe.atr_ratio is not None and (
            not math.isfinite(timeframe.atr_ratio) or timeframe.atr_ratio <= 0
        ):
            reject(
                "atr_ratio_invalid", "Supplied ATR ratio must be finite and positive"
            )
        if reasons:
            return finish()

        evidence(
            "volatility",
            {
                "atr": timeframe.atr,
                "atr_ratio": timeframe.atr_ratio,
                "regime": timeframe.volatility_regime,
            },
            config.min_atr <= timeframe.atr <= config.max_atr
            and timeframe.volatility_regime != "high"
            and (
                timeframe.atr_ratio is None
                or timeframe.atr_ratio <= config.max_atr_ratio
            ),
            "volatility_unsuitable",
            f"ATR must be {config.min_atr:g}–{config.max_atr:g}; high regime or ratio above {config.max_atr_ratio:g} is rejected",
        )
        setup = self._detect_setup(market, timeframe, supporting, conflicting, reject)
        if setup is None or not setup.valid:
            return finish()
        self._indicator_evidence(
            market.price, timeframe, setup, supporting, conflicting
        )
        opposing = self._opposing_level(data, setup.side)
        if opposing is None:
            reject(
                "opposing_structure_missing",
                f"No fresh supplied {'resistance above' if setup.side == 1 else 'support below'} entry {market.price:g}; target room cannot be verified",
            )
            return finish()
        target_level, target_interval = opposing
        supporting.append(
            StrategySignal(
                name="structural_references",
                values={
                    "anchor": setup.anchor,
                    "anchor_timeframe": timeframe.interval,
                    "opposing_level": target_level,
                    "opposing_timeframe": target_interval,
                },
            )
        )
        direction, side = setup.direction, setup.side

        # Derive from market structure/ATR first. Configured dollar bounds only
        # validate the resulting candidate; they never clamp it into eligibility.
        with localcontext() as context:
            context.prec = 40
            entry, atr = _decimal(market.price), _decimal(timeframe.atr)
            buffer = atr * _decimal(config.structure_buffer_atr)
            structure_stop = side * (entry - _decimal(setup.anchor))
            target_room = side * (_decimal(target_level) - entry)
            stop_distance = max(
                structure_stop + buffer, atr * _decimal(config.stop_atr_multiplier)
            )
            target_distance = min(
                target_room - buffer, atr * _decimal(config.target_atr_multiplier)
            )
            stop, target = entry - side * stop_distance, entry + side * target_distance
            supporting.append(
                StrategySignal(
                    name="candidate_levels",
                    values={
                        "entry": float(entry),
                        "atr_buffer": float(buffer),
                        "stop_loss": float(stop),
                        "take_profit": float(target),
                        "stop_distance": float(stop_distance),
                        "target_distance": float(target_distance),
                        "unbuffered_target_room": float(target_room),
                    },
                )
            )
            for name, distance, low, high in (
                (
                    "stop",
                    stop_distance,
                    config.min_stop_distance,
                    config.max_stop_distance,
                ),
                (
                    "target",
                    target_distance,
                    config.min_target_distance,
                    config.max_target_distance,
                ),
            ):
                if distance < _decimal(low):
                    reject(
                        f"{name}_below_minimum",
                        f"Derived {name} distance ${distance} is below ${low:g}",
                        float(distance),
                        low,
                    )
                elif distance > _decimal(high):
                    reject(
                        f"{name}_above_maximum",
                        f"Derived {name} distance ${distance} exceeds ${high:g}",
                        float(distance),
                        high,
                    )
            if target_room - buffer < _decimal(config.min_target_distance):
                level = "resistance" if side == 1 else "support"
                reject(
                    f"nearby_{level}",
                    f"Nearby {level} leaves insufficient room after ATR buffer",
                    float(target_room - buffer),
                    config.min_target_distance,
                )
            if stop <= 0 or target <= 0:
                reject(
                    "protection_price_invalid",
                    "Derived SL/TP price must remain positive",
                )
            cost, reward_risk = self._execution_checks(
                data, stop_distance, target_distance, supporting, reject, check_time
            )
            if reasons:
                return finish()
            return finish(
                StrategyTradeProposal(
                    direction=direction,
                    entry_reference=float(entry),
                    stop_loss=float(stop),
                    take_profit=float(target),
                    stop_distance=float(stop_distance),
                    target_distance=float(target_distance),
                    max_holding_minutes=config.max_holding_minutes,
                    estimated_round_trip_cost=float(cost),
                    net_reward_risk=float(reward_risk),
                )
            )

    def _detect_setup(self, market, timeframe, supporting, conflicting, reject):
        price, atr = market.price, timeframe.atr
        action = timeframe.price_action
        reaction = getattr(action, "rejection", None)
        displacement = getattr(action, "displacement_direction", None)
        detected = getattr(action, "displacement_detected", False)
        strength = getattr(action, "displacement_strength", None)
        values = {
            "price": price,
            "support": timeframe.support,
            "resistance": timeframe.resistance,
            "structure": timeframe.structure,
            "high_pattern": timeframe.high_pattern,
            "low_pattern": timeframe.low_pattern,
            "rejection": reaction,
            "structure_break": timeframe.structure_break,
            "displacement_direction": displacement,
            "displacement_detected": "yes" if detected else "no",
            "displacement_strength": strength,
        }
        if timeframe.structure_break in ("break_above", "break_below"):
            long = timeframe.structure_break == "break_above"
            side, direction = (1, "long") if long else (-1, "short")
            anchor = timeframe.resistance if long else timeframe.support
            valid = True
            if anchor is None:
                reject(
                    "breakout_level_missing",
                    "Break marker has no measured broken level",
                )
                valid = False
            else:
                extension = float(
                    side * (_decimal(price) - _decimal(anchor)) / _decimal(atr)
                )
                values["distance_from_broken_level_atr"] = extension
                if extension <= 0:
                    reject(
                        "breakout_not_holding",
                        f"{direction} breakout price {price:g} is not beyond measured level {anchor:g}",
                        price,
                        anchor,
                    )
                    valid = False
                elif extension > self.config.max_breakout_distance_atr:
                    reject(
                        "breakout_overextended",
                        "Entry is too far beyond the observed broken level",
                        extension,
                        self.config.max_breakout_distance_atr,
                    )
                    valid = False
            expected = "bullish" if long else "bearish"
            if not (
                detected
                and displacement == expected
                and strength is not None
                and math.isfinite(strength)
                and strength > 0
            ):
                reject(
                    "breakout_displacement_missing",
                    f"{direction} break requires measured {expected} displacement; observed detected={detected}, direction={displacement}, strength={strength}",
                )
                valid = False
            if reaction == ("bearish_rejection" if long else "bullish_rejection"):
                reject(
                    "breakout_counter_rejection",
                    f"Latest rejection opposes the {direction} breakout",
                )
                valid = False
            (supporting if valid else conflicting).append(
                StrategySignal(name="breakout_confirmation", values=values)
            )
            return _Setup("breakout", direction, anchor, valid)

        if reaction not in ("bullish_rejection", "bearish_rejection"):
            conflicting.append(
                StrategySignal(name="structural_entry_evidence", values=values)
            )
            reject(
                "reaction_evidence_missing",
                f"No bullish/bearish rejection near a measured boundary and no confirmed breakout; observed rejection={reaction}",
            )
            return None
        long = reaction == "bullish_rejection"
        side, direction = (1, "long") if long else (-1, "short")
        anchor = timeframe.support if long else timeframe.resistance
        ema20 = timeframe.ema_20
        near_ema = (
            ema20 is not None
            and math.isfinite(ema20)
            and ema20 > 0
            and abs(_decimal(price) - _decimal(ema20)) / _decimal(atr)
            <= _decimal(self.config.max_entry_distance_atr)
        )
        trend = "bullish" if long else "bearish"
        pullback = (
            timeframe.market_regime != "ranging"
            and near_ema
            and (timeframe.trend_direction == trend or timeframe.structure == trend)
        )
        kind = (
            "trend_pullback_continuation"
            if pullback
            else ("range_support_bounce" if long else "range_resistance_rejection")
        )
        valid = True
        if anchor is None:
            reject(
                "setup_anchor_missing",
                f"{kind} has no measured {'support' if long else 'resistance'} anchor",
            )
            valid = False
        else:
            distance = float(
                side * (_decimal(price) - _decimal(anchor)) / _decimal(atr)
            )
            values["distance_to_reaction_level_atr"] = distance
            if (
                distance < 0
                or (timeframe.support is not None and price < timeframe.support)
                or (timeframe.resistance is not None and price > timeframe.resistance)
            ):
                reject(
                    "price_outside_observed_range",
                    f"Rejection entry {price:g} lies beyond supplied support/resistance; no confirmed break supports that entry",
                )
                valid = False
            elif distance > self.config.max_structure_distance_atr:
                reject(
                    "reaction_too_far_from_level",
                    f"{reaction} is {distance:g} ATR from the measured anchor, not a nearby structural entry",
                    distance,
                    self.config.max_structure_distance_atr,
                )
                valid = False
        if detected and displacement == ("bearish" if long else "bullish"):
            reject(
                "reaction_counter_displacement",
                f"Measured {displacement} displacement invalidates the {direction} rejection setup",
            )
            valid = False
        values.update(
            market_regime=timeframe.market_regime,
            trend_direction=timeframe.trend_direction,
            ema_20=ema20 if ema20 is not None and math.isfinite(ema20) else None,
        )
        (supporting if valid else conflicting).append(
            StrategySignal(name="structural_reaction", values=values)
        )
        return _Setup(kind, direction, anchor, valid)

    def _opposing_level(self, data, side):
        # A broken latest swing is not an overhead/underlying target. Use another
        # actually supplied level, never extrapolate a replacement from ATR.
        levels = []
        for timeframe in data.analysis.timeframes:
            if not _aware(timeframe.latest_candle_timestamp):
                continue
            age = (
                data.evaluated_at - timeframe.latest_candle_timestamp
            ).total_seconds()
            if age < 0 or age > self.config.max_opposing_level_age_seconds:
                continue
            level = timeframe.resistance if side == 1 else timeframe.support
            if (
                level is not None
                and math.isfinite(level)
                and level > 0
                and side * (level - data.analysis.price) >= 0
            ):
                levels.append((level, timeframe.interval))
        return (
            min(levels, key=lambda item: side * (item[0] - data.analysis.price))
            if levels
            else None
        )

    @staticmethod
    def _indicator_evidence(price, timeframe, setup, supporting, conflicting):
        values = {
            name: getattr(timeframe, name) for name in ("ema_9", "ema_20", "ema_50")
        }
        valid = all(
            value is not None and math.isfinite(value) and value > 0
            for value in values.values()
        )
        aligned = (
            valid
            and setup.side * (timeframe.ema_9 - timeframe.ema_20) > 0
            and setup.side * (timeframe.ema_20 - timeframe.ema_50) > 0
            and timeframe.trend_direction
            == ("bullish" if setup.side == 1 else "bearish")
            and timeframe.ema_20_slope == ("rising" if setup.side == 1 else "falling")
            and timeframe.ema_50_slope == ("rising" if setup.side == 1 else "falling")
        )
        values = {
            name: value if value is not None and math.isfinite(value) else None
            for name, value in values.items()
        }
        values.update(
            price=price,
            distance_to_ema20_atr=(
                float(
                    abs(_decimal(price) - _decimal(timeframe.ema_20))
                    / _decimal(timeframe.atr)
                )
                if valid
                else None
            ),
            ema_20_slope=timeframe.ema_20_slope,
            ema_50_slope=timeframe.ema_50_slope,
            trend_direction=timeframe.trend_direction,
        )
        (supporting if aligned else conflicting).append(
            StrategySignal(name="ema_momentum", values=values)
        )
        pattern_matches = (
            timeframe.low_pattern == "higher_low"
            if setup.side == 1
            else timeframe.high_pattern == "lower_high"
        )
        (supporting if pattern_matches else conflicting).append(
            StrategySignal(
                name="structure_context",
                values={
                    "structure": timeframe.structure,
                    "high_pattern": timeframe.high_pattern,
                    "low_pattern": timeframe.low_pattern,
                },
            )
        )

    def _execution_checks(self, data, stop, target, signals, reject, check_time):
        execution, config = data.execution, self.config
        if execution is None:
            reject(
                "execution_evidence_missing",
                "Reliable spread, costs and normalized broker distance limits are required",
            )
            return None, None
        check_time(execution.observed_at, "execution", config.max_execution_age_seconds)
        values = {
            name: getattr(execution, name)
            for name in (
                "spread",
                "additional_round_trip_cost",
                "min_stop_distance",
                "min_target_distance",
            )
        }
        signals.append(
            StrategySignal(
                name="execution_observations",
                values={"source": execution.source, **values},
            )
        )
        missing = [name for name, value in values.items() if value is None]
        for name in missing:
            reject(
                "execution_measurement_missing",
                f"Execution evidence is missing {name}",
                name,
            )
        if missing:
            return None, None
        if execution.spread > config.max_spread:
            reject(
                "spread_excessive",
                "Observed spread exceeds the strategy limit",
                execution.spread,
                config.max_spread,
            )
        for name, distance, minimum in (
            ("stop", stop, execution.min_stop_distance),
            ("target", target, execution.min_target_distance),
        ):
            if distance < _decimal(minimum):
                reject(
                    f"broker_{name}_minimum",
                    f"Derived {name} distance violates broker minimum",
                    float(distance),
                    minimum,
                )
        cost = _decimal(execution.spread) + _decimal(
            execution.additional_round_trip_cost
        )
        if target <= 0 or cost > target * _decimal(config.max_cost_fraction_of_target):
            reject(
                "execution_cost_excessive",
                "Round-trip costs consume too much of the target movement",
                float(cost),
                float(target * _decimal(config.max_cost_fraction_of_target)),
            )
        reward_risk = (target - cost) / (stop + cost)
        signals.append(
            StrategySignal(
                name="net_reward_risk",
                values={
                    "total_cost": float(cost),
                    "net_reward_risk": float(reward_risk),
                },
            )
        )
        if reward_risk < _decimal(config.min_net_reward_risk):
            reject(
                "net_reward_risk_insufficient",
                "Net reward / risk after execution costs is below the research threshold",
                float(reward_risk),
                config.min_net_reward_risk,
            )
        return cost, reward_risk
