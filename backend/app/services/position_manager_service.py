from datetime import datetime, timezone

from app.providers.broker_base import BrokerProvider
from app.services.market_service import MarketService


class PositionManagerService:
    def __init__(
        self,
        broker_provider: BrokerProvider,
        market_service: MarketService,
    ):
        self.broker_provider = broker_provider
        self.market_service = market_service

    def manage_open_positions(self):
        positions = self.broker_provider.get_open_positions()

        closed_positions = []

        for position in positions:
            quote = self.market_service.get_price(position.symbol)
            current_price = quote.price

            close_reason = None

            if position.direction == "long":
                if (
                    position.stop_loss is not None
                    and current_price <= position.stop_loss
                ):
                    close_reason = "stop_loss"

                elif (
                    position.take_profit is not None
                    and current_price >= position.take_profit
                ):
                    close_reason = "take_profit"

            elif position.direction == "short":
                if (
                    position.stop_loss is not None
                    and current_price >= position.stop_loss
                ):
                    close_reason = "stop_loss"

                elif (
                    position.take_profit is not None
                    and current_price <= position.take_profit
                ):
                    close_reason = "take_profit"

            if close_reason is None and position.max_holding_minutes is not None:
                now = datetime.now(timezone.utc)

                holding_minutes = (now - position.opened_at).total_seconds() / 60

                if holding_minutes >= position.max_holding_minutes:
                    close_reason = "max_holding_time"

            if close_reason is not None:
                closed_position = self.broker_provider.close_position(
                    position=position,
                    exit_price=current_price,
                    close_reason=close_reason,
                )

                closed_positions.append(closed_position)

        return closed_positions
