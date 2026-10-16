def market_structure_fields(current_price: float = 4200.0) -> dict:
    """Return fresh neutral structure values for tests of unrelated behavior."""
    support = 4100.0
    resistance = 4300.0
    return {
        "structure": "mixed",
        "high_pattern": "higher_high",
        "low_pattern": "lower_low",
        "structure_break": "none",
        "support": support,
        "resistance": resistance,
        "distance_to_swing_high": round(resistance - current_price, 5),
        "distance_to_swing_low": round(current_price - support, 5),
    }
