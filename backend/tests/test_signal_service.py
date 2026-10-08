from app.services.signal_service import SignalService


def test_rsi_falling_below_30_is_bearish():
    service = SignalService()

    result = service.calculate_score(
        rsi=28,
        rsi_change=-3,
        macd_histogram=0,
        ema_20=100,
        ema_50=100,
        atr=1,
    )

    assert result["breakdown"]["rsi"] == -8.0


def test_rsi_rising_below_30_is_recovery():
    service = SignalService()

    result = service.calculate_score(
        rsi=28,
        rsi_change=3,
        macd_histogram=0,
        ema_20=100,
        ema_50=100,
        atr=1,
    )

    assert result["breakdown"]["rsi"] == 4.0
