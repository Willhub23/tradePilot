import json

from datetime import datetime, timezone
from pathlib import Path

from app.models.agent_models import AgentDecision, AgentMarketContext


class AgentDecisionLogService:
    def __init__(
        self,
        log_path: str = "data/agent_decisions.jsonl",
    ):
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

    def log_decision(
        self,
        context: AgentMarketContext,
        decision: AgentDecision,
    ) -> None:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "context": context.model_dump(mode="json"),
            "decision": decision.model_dump(mode="json"),
        }

        with self.log_path.open("a") as file:
            file.write(json.dumps(log_entry) + "\n")
