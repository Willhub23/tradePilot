import json

import httpx
from pydantic import ValidationError

from app.models.agent_models import AgentDecision, AgentMarketContext


class LocalReasoningProvider:
    MAX_ATTEMPTS = 2

    def __init__(
        self,
        model: str = "qwen3:1.7b",
        base_url: str = "http://127.0.0.1:11434",
    ):
        self.model = model
        self.base_url = base_url

    def make_decision(
        self,
        context: AgentMarketContext,
    ) -> AgentDecision:

        system_prompt = """
You are the reasoning engine for TradePilot.

Analyze the supplied market context and decide whether there is a sufficiently
strong trading opportunity.

Choose exactly one action:
- long
- short
- hold

Choose exactly one trade style:
- intraday
- swing
- none

Use only the supplied market data.
Do not invent prices, indicators, events, or market conditions.

A HOLD decision is valid and should be preferred when the evidence is weak,
contradictory, or unclear.

Evaluate evidence across timeframes rather than mechanically following a
single indicator.

Return valid JSON only.

The response must contain exactly these fields:

action
trade_style
confidence
thesis
invalidation_reason
target_reason
expected_horizon

confidence must be between 0 and 100.

Do not wrap the JSON in markdown.
Do not add commentary before or after the JSON.
"""

        if context.strategy_evaluation is not None:
            system_prompt += """
This is a Quick Trade proposal review, not independent trade generation.
Assess only strategy_evaluation.proposal and its measured supporting/conflicting
signals. Approve its exact direction with trade_style=intraday, or choose hold.
Do not substitute swing levels, change SL/TP, or extend maximum holding time.
Explain whether the supplied short-term setup is justified; never invent levels.
"""

        context_json = context.model_dump_json(
            exclude={"strategy_evaluation", "opportunity_id"} if context.strategy_evaluation is None else None)
        print("=== AGENT PROMPT ===")
        print(system_prompt)
        for attempt in range(self.MAX_ATTEMPTS):
            response = httpx.post(
                f"{self.base_url}/api/chat",
                json={
                    "model": self.model,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "num_ctx": 8192,
                    },
                    "messages": [
                        {
                            "role": "system",
                            "content": system_prompt,
                        },
                        {
                            "role": "user",
                            "content": (
                                "Analyze this market context:\n\n"
                                + context_json
                            ),
                        },
                    ],
                },
                timeout=300,
            )

            response.raise_for_status()

            result = response.json()

            raw_decision = result["message"]["content"]

            try:
                decision_data = json.loads(raw_decision)

                # Occasionally a model may return JSON encoded as a string.
                if isinstance(decision_data, str):
                    decision_data = json.loads(decision_data)

                return AgentDecision.model_validate(decision_data)

            except (json.JSONDecodeError, ValidationError, TypeError) as exc:
                print(
                    f"[LLM] Invalid response "
                    f"(attempt {attempt + 1}/{self.MAX_ATTEMPTS}): {exc}"
                )
                print(f"[LLM] Raw output: {raw_decision}")

                print("=== RAW AGENT RESPONSE ===")
                print(response)

        return AgentDecision(
            action="hold",
            trade_style="none",
            confidence=0.0,
            thesis="Local reasoning model returned invalid structured output.",
            invalidation_reason=None,
            target_reason=None,
            expected_horizon=None,
        )
