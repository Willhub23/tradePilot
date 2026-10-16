from datetime import datetime, timezone

from app.models.agent_models import AgentDecision, AgentMarketContext
from app.models.market_models import (
    MultiTimeframeAnalysis,
    TimeframeAnalysis,
)
from app.services.grounding_validator import GroundingValidator
from tests.market_fixtures import market_structure_fields
from tests.macro_fixtures import agent_macro_fields



def test_rejects_unsupported_macro_direction():
    market_analysis = MultiTimeframeAnalysis(
        symbol="XAUUSD",
        price=4180.0,
        price_timestamp=datetime.now(timezone.utc),
        timeframes=[
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="5min",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=45.0,
                ema_20=4179.0,
                ema_50=4181.0,
                atr=2.0,
                score=45.0,
                bias="neutral",
                trend_direction="bearish",
                trend_strength=0.5,
                trend_strength_label="moderate",
            ),
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="15min",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=55.0,
                ema_20=4182.0,
                ema_50=4175.0,
                atr=5.0,
                score=60.0,
                bias="neutral",
                trend_direction="bullish",
                trend_strength=1.4,
                trend_strength_label="strong",
            ),
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="1h",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=65.0,
                ema_20=4185.0,
                ema_50=4168.0,
                atr=12.0,
                score=64.0,
                bias="neutral",
                trend_direction="bullish",
                trend_strength=1.5,
                trend_strength_label="strong",
            ),
        ],
        overall_score=58.0,
        overall_bias="neutral",
        timeframe_alignment="aligned",
    )

    context = AgentMarketContext(
        symbol="XAUUSD",
        market_analysis=market_analysis,
        **agent_macro_fields(),
    )

    decision = AgentDecision(
        action="long",
        trade_style="swing",
        confidence=75,
        thesis="The macro event could push gold higher.",
        invalidation_reason=None,
        target_reason=None,
        expected_horizon="1-2 days",
    )

    result = GroundingValidator().validate(
        context=context,
        decision=decision,
    )

    assert result.action == "hold"
    assert result.confidence == 0
    assert result.trade_style == "none"


def test_allows_non_directional_macro_risk():
    market_analysis = MultiTimeframeAnalysis(
        symbol="XAUUSD",
        price=4180.0,
        price_timestamp=datetime.now(timezone.utc),
        timeframes=[
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="5min",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=45.0,
                ema_20=4179.0,
                ema_50=4181.0,
                atr=2.0,
                score=45.0,
                bias="neutral",
                trend_direction="bearish",
                trend_strength=0.5,
                trend_strength_label="moderate",
            ),
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="15min",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=55.0,
                ema_20=4182.0,
                ema_50=4175.0,
                atr=5.0,
                score=60.0,
                bias="neutral",
                trend_direction="bullish",
                trend_strength=1.4,
                trend_strength_label="strong",
            ),
            TimeframeAnalysis(
                **market_structure_fields(current_price=4180.0),
                interval="1h",
                latest_candle_timestamp=datetime.now(timezone.utc),
                rsi=65.0,
                ema_20=4185.0,
                ema_50=4168.0,
                atr=12.0,
                score=64.0,
                bias="neutral",
                trend_direction="bullish",
                trend_strength=1.5,
                trend_strength_label="strong",
            ),
        ],
        overall_score=58.0,
        overall_bias="neutral",
        timeframe_alignment="aligned",
    )

    context = AgentMarketContext(
        symbol="XAUUSD",
        market_analysis=market_analysis,
        **agent_macro_fields(),
    )

    decision = AgentDecision(
        action="hold",
        trade_style="none",
        confidence=60,
        thesis="The upcoming macro event may increase volatility.",
        invalidation_reason=None,
        target_reason=None,
        expected_horizon=None,
    )

    result = GroundingValidator().validate(
        context=context,
        decision=decision,
    )

    assert result.action == "hold"
    assert result.confidence == 60
    assert result.thesis == "The upcoming macro event may increase volatility."
