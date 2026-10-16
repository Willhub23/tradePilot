import re

from app.models.agent_models import AgentDecision, AgentMarketContext


class GroundingValidator:
    def validate(
        self,
        context: AgentMarketContext,
        decision: AgentDecision,
    ) -> AgentDecision:
        event_name = context.macro_context["event_name"]

        if not event_name:
            return decision

        text = " ".join(
            filter(
                None,
                [
                    decision.thesis,
                    decision.target_reason,
                    decision.invalidation_reason,
                ],
            )
        ).lower()

        macro_references = [
            "macro event",
            "economic event",
            event_name.lower(),
        ]

        directional_words = [
            "bullish",
            "bearish",
            "push",
            "drive",
            "rise",
            "fall",
            "higher",
            "lower",
            "upward",
            "downward",
            "up",
            "down",
            "further",
        ]

        references_macro = any(reference in text for reference in macro_references)

        claims_direction = any(
            re.search(rf"\b{re.escape(word)}\b", text) for word in directional_words
        )

        if references_macro and claims_direction:
            return AgentDecision(
                action="hold",
                trade_style="none",
                confidence=0,
                thesis=(
                    "Decision rejected because the model assigned a directional "
                    "effect to a macro event without forecast, actual, previous, "
                    "or other directional macro data."
                ),
                invalidation_reason=None,
                target_reason=None,
                expected_horizon=None,
            )

        return decision
