from app.services.macro_context_service import MacroContextService
from app.services.evidence_priority_service import EvidencePriorityService
from datetime import datetime, timezone


from app.models.agent_models import AgentMarketContext
from app.services.agent_service import AgentService
from app.services.analysis_service import AnalysisService
from app.services.macro_service import MacroService
from app.providers.local_reasoning_provider import LocalReasoningProvider
from app.models.agent_models import AgentResult
from app.services.trade_proposal_service import TradeProposalService
from app.services.risk_service import RiskService
from app.providers.broker_base import BrokerProvider
from app.services.market_session_service import MarketSessionService


class TradingCycleService:
    def __init__(
        self,
        analysis_service: AnalysisService,
        macro_service: MacroService,
        broker_provider: BrokerProvider,
    ):
        self.analysis_service = analysis_service
        self.macro_service = macro_service
        self.broker_provider = broker_provider
        self.agent_service = AgentService(reasoning_provider=LocalReasoningProvider())
        self.trade_proposal_service = TradeProposalService()
        self.risk_service = RiskService()
        self.market_session_service = MarketSessionService()
        self.macro_context_service = MacroContextService()
        self.evidence_priority_service = EvidencePriorityService()

    def run(self, symbol: str) -> AgentResult:
        market_analysis = self.analysis_service.analyze_multi_timeframe(
            symbol=symbol,
        )

        events = self.macro_service.get_events()

        macro_context = self.macro_context_service.build_context(
            events=events,
            now=datetime.now(timezone.utc),
        )

        evidence_priority = self.evidence_priority_service.prioritize(
            macro_context=macro_context,
        )

        context = AgentMarketContext(
            symbol=symbol.upper(),
            market_analysis=market_analysis,
            macro_context=macro_context,
            evidence_priority=evidence_priority,
        )

        print("=== AGENT CONTEXT ===")
        print(context.model_dump_json(indent=2))

        result = self.agent_service.make_decision(context)
        decision = result.decision

        proposal = self.trade_proposal_service.create_proposal(
            context=context,
            decision=decision,
            agent_decision_id=result.decision_id,
        )

        approved = self.risk_service.approve(proposal)

        execution_block_reason = None

        position = None

        if approved and proposal is not None:
            open_positions = self.broker_provider.get_open_positions()

            existing_position = next(
                (
                    p
                    for p in open_positions
                    if p.symbol == context.symbol and p.status == "open"
                ),
                None,
            )

            market_open = self.market_session_service.is_market_open()

            quote_fresh = self.market_session_service.is_quote_fresh(
                market_analysis.price_timestamp,
            )

            if existing_position is not None:
                execution_block_reason = "position_already_open"

            elif not market_open:
                execution_block_reason = "market_closed"

            elif not quote_fresh:
                execution_block_reason = "stale_quote"

            else:
                entry_price = market_analysis.price

                one_hour = next(
                    (
                        timeframe
                        for timeframe in market_analysis.timeframes
                        if timeframe.interval == "1h"
                    ),
                    None,
                )

                if one_hour is None:
                    execution_block_reason = "missing_1h_analysis"

                else:
                    atr = one_hour.atr

                    if proposal.direction == "long":
                        stop_loss = entry_price - (1.5 * atr)
                        take_profit = entry_price + (2.0 * atr)

                    else:
                        stop_loss = entry_price + (1.5 * atr)
                        take_profit = entry_price - (2.0 * atr)

                    if proposal.trade_style == "intraday":
                        max_holding_minutes = 360
                    else:
                        max_holding_minutes = 2880

                    position = self.broker_provider.open_position(
                        proposal=proposal,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        max_holding_minutes=max_holding_minutes,
                    )

        return AgentResult(
            decision_id=result.decision_id,
            decision=decision,
            proposal=proposal,
            approved=approved,
            position=position,
            execution_block_reason=execution_block_reason,
        )
