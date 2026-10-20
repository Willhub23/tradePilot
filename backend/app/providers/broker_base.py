from typing import Protocol

from app.models.execution_models import (
    BrokerExecutionRequest,
    BrokerExecutionResult,
)
from app.models.broker_models import BrokerPosition, BrokerCloseResult
from app.models.paper_trading_models import PaperPosition


class BrokerProvider(Protocol):
    def get_open_positions(self) -> list[BrokerPosition | PaperPosition]: ...

    def open_position(
        self,
        request: BrokerExecutionRequest,
    ) -> BrokerExecutionResult: ...

    def close_position(
        self,
        position: PaperPosition | BrokerPosition,
        exit_price: float,
        close_reason: str | None = None,
    ) -> PaperPosition | BrokerCloseResult: ...
