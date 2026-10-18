from app.models.execution_models import (
    BrokerExecutionRequest,
    BrokerExecutionResult,
)
from app.models.paper_trading_models import PaperPosition
from app.models.trade_models import TradeProposal
from app.services.paper_trading_service import PaperTradingService


class LocalPaperBrokerProvider:
    def __init__(
        self,
        paper_trading_service: PaperTradingService,
    ):
        self.paper_trading_service = paper_trading_service

    def get_open_positions(self) -> list[PaperPosition]:
        return self.paper_trading_service.get_open_positions()

    def open_position(
        self,
        request: BrokerExecutionRequest,
    ) -> BrokerExecutionResult:
        proposal = TradeProposal(
            agent_decision_id=request.agent_decision_id,
            symbol=request.symbol,
            direction=request.direction,
            trade_style=request.trade_style,
            confidence=request.confidence,
            thesis=request.thesis,
        )

        position = self.paper_trading_service.open_position(
            proposal=proposal,
            entry_price=request.reference_price,
            stop_loss=request.stop_loss,
            take_profit=request.take_profit,
            max_holding_minutes=request.max_holding_minutes,
        )

        return BrokerExecutionResult(
            request_id=request.request_id,
            status="filled",
            broker_order_id=None,
            broker_position_id=position.id,
            filled_volume_lots=request.volume_lots,
            fill_price=position.entry_price,
        )

    def close_position(
        self,
        position: PaperPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition:
        return self.paper_trading_service.close_position(
            position=position,
            exit_price=exit_price,
            close_reason=close_reason,
        )
