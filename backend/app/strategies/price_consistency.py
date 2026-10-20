"""Compare independent feeds without rebasing prices or structural levels."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


def aware_time(value):
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
        if value.tzinfo is None or value.utcoffset() is None:
            return None
        return value.astimezone(timezone.utc)
    except (AttributeError, TypeError, ValueError):
        return None


def compare_prices(reference, bid, ask, reference_time, broker_time, *, target_distance=None, max_time_skew=5):
    """Comparability is receipt/event proximity, NOT proof of market-event alignment."""
    result = {"status": "missing_or_invalid_prices", "market_event_alignment_confirmed": False,
              "timestamp_semantics": "analysis_provider_receipt_vs_broker_event",
              "actual_tp_fraction": None}
    try:
        ref, low, high = [Decimal(str(value)) for value in (reference, bid, ask)]
        if not all(value.is_finite() and value > 0 for value in (ref, low, high)) or high < low:
            return result
    except (InvalidOperation, ValueError, TypeError):
        return result
    midpoint = (low+high)/2
    gap = abs(ref-midpoint)
    result.update(reference=float(ref), bid=float(low), ask=float(high), midpoint=float(midpoint),
                  reference_minus_mid=float(ref-midpoint), absolute_mid_gap=float(gap),
                  reference_minus_bid=float(ref-low), reference_minus_ask=float(ref-high),
                  nearest_executable_gap=float(max(low-ref, ref-high, Decimal(0))))
    ref_time, observed = aware_time(reference_time), aware_time(broker_time)
    if ref_time is None or observed is None:
        result["status"] = "missing_or_naive_timestamps"
        return result
    result.update(reference_timestamp=ref_time.isoformat(), broker_timestamp=observed.isoformat(),
                  time_skew_seconds=abs((ref_time-observed).total_seconds()))
    result["status"] = "comparable" if result["time_skew_seconds"] <= max_time_skew else "timestamp_skew_exceeded"
    if target_distance is not None:
        try:
            distance = Decimal(str(target_distance))
            if distance.is_finite() and distance > 0:
                result["actual_tp_distance"] = float(distance)
                result["actual_tp_fraction"] = float(gap/distance)
        except (InvalidOperation, ValueError, TypeError):
            pass
    return result
