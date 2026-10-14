from app.models.trade_models import TradeProposal


class RiskService:
    MIN_CONFIDENCE = 60.0

    def approve(
        self,
        proposal: TradeProposal | None,
    ) -> bool:
        if proposal is None:
            return False

        if proposal.confidence < self.MIN_CONFIDENCE:
            return False

        return True
