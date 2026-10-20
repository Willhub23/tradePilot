"""Independent research strategies; no runtime or broker wiring."""
from app.strategies.quick_trade import QuickTradeStrategy
from app.strategies.quick_trade_config import QuickTradeConfig

__all__ = ["QuickTradeStrategy", "QuickTradeConfig"]
