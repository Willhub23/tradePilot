"""Execution-only checks for unchanged Quick Trade protection after quote refresh."""

from decimal import Decimal
from app.strategies.quick_trade_config import QuickTradeConfig


def validate_preserved_levels(
    *,
    direction,
    entry,
    original_entry,
    stop_loss,
    take_profit,
    stop_budget,
    snapshot,
    additional_cost,
    max_price_drift
):
    limits = QuickTradeConfig()
    if snapshot.bid is None or snapshot.ask is None or snapshot.ask < snapshot.bid:
        raise ValueError("current_broker_quote_unavailable")
    if abs(entry - original_entry) > max_price_drift:
        raise ValueError("price_drift_exceeded")
    if (
        snapshot.min_stop_distance is None
        or snapshot.min_target_distance is None
        or additional_cost is None
    ):
        raise ValueError("execution_constraints_or_costs_unavailable")
    side = Decimal(1 if direction == "long" else -1)
    stop = side * (Decimal(str(entry)) - Decimal(str(stop_loss)))
    target = side * (Decimal(str(take_profit)) - Decimal(str(entry)))
    spread = Decimal(str(snapshot.ask)) - Decimal(str(snapshot.bid))
    cost = spread + Decimal(str(additional_cost))
    if (
        spread > Decimal(str(limits.max_spread))
        or not Decimal(str(limits.min_stop_distance))
        <= stop
        <= Decimal(str(stop_budget))
        or not Decimal(str(limits.min_target_distance))
        <= target
        <= Decimal(str(limits.max_target_distance))
        or stop < Decimal(str(snapshot.min_stop_distance))
        or target < Decimal(str(snapshot.min_target_distance))
        or cost > target * Decimal(str(limits.max_cost_fraction_of_target))
        or (target - cost) / (stop + cost) < Decimal(str(limits.min_net_reward_risk))
    ):
        raise ValueError("original_protection_no_longer_valid")
