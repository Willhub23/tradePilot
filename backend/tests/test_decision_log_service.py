from app.models.market_models import MacdValue, MarketAnalysis
from app.services.decision_log_service import DecisionLogService


def test_decision_log_service_appends_entries(tmp_path):
    log_file = tmp_path / "decision_logs.jsonl"

    service = DecisionLogService(
        log_path=str(log_file),
    )

    analysis = MarketAnalysis(
        symbol="XAUUSD",
        interval="5min",
        price=4100.0,
        rsi=55.0,
        macd=MacdValue(
            symbol="XAUUSD",
            indicator="MACD",
            macd=1.0,
            signal=0.5,
            histogram=0.5,
            interval="5min",
            fast_period=12,
            slow_period=26,
            signal_period=9,
        ),
        ema_20=4101.0,
        ema_50=4099.0,
        atr=4.0,
        trend_strength=0.5,
        trend_strength_label="moderate",
        score=60.0,
        bias="neutral",
        score_breakdown={
            "base": 50.0,
            "rsi": 0.0,
            "macd": 2.5,
            "trend": 7.5,
        },
        signals={
            "rsi": "neutral",
            "macd": "bullish",
            "trend": "bullish",
        },
    )

    service.log_analysis(analysis)
    service.log_analysis(analysis)

    lines = log_file.read_text().strip().splitlines()

    assert len(lines) == 2