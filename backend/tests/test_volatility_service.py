from app.services.volatility_service import VolatilityService


def test_empty_values_returns_uncertain():
    service = VolatilityService()

    result = service.calculate_regime([])

    assert result == {
        "current_atr": None,
        "baseline_atr": None,
        "atr_ratio": None,
        "volatility_regime": "uncertain",
    }


def test_single_value_returns_uncertain():
    service = VolatilityService()

    result = service.calculate_regime([10.0])

    assert result["current_atr"] == 10.0
    assert result["baseline_atr"] is None
    assert result["atr_ratio"] is None
    assert result["volatility_regime"] == "uncertain"


def test_low_volatility():
    service = VolatilityService()

    result = service.calculate_regime([10, 10, 10, 6])

    assert result["volatility_regime"] == "low"


def test_normal_volatility():
    service = VolatilityService()

    result = service.calculate_regime([10, 10, 10, 11])

    assert result["volatility_regime"] == "normal"


def test_elevated_volatility():
    service = VolatilityService()

    result = service.calculate_regime([10, 10, 10, 14])

    assert result["volatility_regime"] == "elevated"


def test_high_volatility():
    service = VolatilityService()

    result = service.calculate_regime([10, 10, 10, 20])

    assert result["volatility_regime"] == "high"


def test_zero_baseline_returns_uncertain():
    service = VolatilityService()

    result = service.calculate_regime([0, 0, 0, 5])

    assert result["atr_ratio"] is None
    assert result["volatility_regime"] == "uncertain"
