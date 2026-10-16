class EvidencePriorityService:
    def prioritize(
        self,
        macro_context: dict,
    ) -> dict:
        state = macro_context["state"]

        if state == "event_imminent":
            return {
                "technical_weight": 0.25,
                "macro_weight": 0.75,
                "priority": [
                    "macro",
                    "volatility",
                    "price_action",
                    "market_structure",
                    "ema",
                    "rsi_macd",
                ],
                "allow_new_trade": False,
            }

        if state == "post_event":
            return {
                "technical_weight": 0.45,
                "macro_weight": 0.55,
                "priority": [
                    "price_action",
                    "volatility",
                    "market_structure",
                    "macro",
                    "ema",
                    "rsi_macd",
                ],
                "allow_new_trade": macro_context["allow_new_trade"],
            }

        if state in {"pre_event", "high_impact_day"}:
            return {
                "technical_weight": 0.60,
                "macro_weight": 0.40,
                "priority": [
                    "macro",
                    "market_structure",
                    "price_action",
                    "volatility",
                    "ema",
                    "rsi_macd",
                ],
                "allow_new_trade": macro_context["allow_new_trade"],
            }

        return {
            "technical_weight": 0.80,
            "macro_weight": 0.20,
            "priority": [
                "market_structure",
                "ema",
                "adx",
                "price_action",
                "volatility",
                "rsi_macd",
                "macro",
            ],
            "allow_new_trade": True,
        }
