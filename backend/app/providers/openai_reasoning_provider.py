import json

from openai import OpenAI

from app.models.agent_models import AgentDecision, AgentMarketContext


class OpenAIReasoningProvider:
    def __init__(self, api_key: str, model: str):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def make_decision(
        self,
        context: AgentMarketContext,
    ) -> AgentDecision:
        system_prompt = """
You are the reasoning engine for TradePilot.

Your job is to analyze the supplied market context and decide whether there is
a sufficiently strong trading opportunity.

You are not an execution engine.

You must choose exactly one action:
- long
- short
- hold

You must also choose exactly one trade style:
- intraday
- swing
- none

Use the supplied market data only.
Do not invent prices, indicators, events, or market conditions.

A HOLD decision is completely valid and should be preferred when evidence is
weak, contradictory, or unclear.

Evaluate evidence across timeframes instead of mechanically following one
indicator.

Return JSON only with these fields:

action
trade_style
confidence
thesis
invalidation_reason
target_reason
expected_horizon

confidence must be between 0 and 100.
"""

        market_context = context.model_dump_json(indent=2)

        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        "Analyze this market context and return a trading decision.\n\n"
                        f"{market_context}"
                    ),
                },
            ],
        )

        raw_output = response.output_text

        decision_data = json.loads(raw_output)

        return AgentDecision(**decision_data)
