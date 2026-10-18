from datetime import datetime, timezone
from uuid import uuid4

from app.services.macro_context_service import MacroContextService
from app.services.evidence_priority_service import EvidencePriorityService


from app.config.trading_config import TRADE_VOLUME_LOTS
from app.models.execution_models import BrokerExecutionRequest


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
from app.services.trade_execution_policy_service import TradeExecutionPolicyService
from app.services.broker_execution_persistence_service import (
    BrokerExecutionPersistenceService,
)


class TradingCycleService:
    def __init__(
        self,
        analysis_service: AnalysisService,
        macro_service: MacroService,
        broker_provider: BrokerProvider,
        execution_persistence: BrokerExecutionPersistenceService | None = None,
    ):
        self.analysis_service = analysis_service
        self.macro_service = macro_service
        self.broker_provider = broker_provider
        self.execution_persistence = execution_persistence
        self.agent_service = AgentService(reasoning_provider=LocalReasoningProvider())
        self.trade_proposal_service = TradeProposalService()
        self.risk_service = RiskService()
        self.market_session_service = MarketSessionService()
        self.macro_context_service = MacroContextService()
        self.evidence_priority_service = EvidencePriorityService()
        self.trade_execution_policy_service = TradeExecutionPolicyService()

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
        execution_result = None

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

            elif self.execution_persistence is not None and any(
                record.symbol.upper() == context.symbol
                for record in self.execution_persistence.get_unresolved()
            ):
                execution_block_reason = "unresolved_broker_execution"

            elif not market_open:
                execution_block_reason = "market_closed"

            elif not quote_fresh:
                execution_block_reason = "stale_quote"

            else:
                entry_price = market_analysis.price

                try:
                    plan = self.trade_execution_policy_service.build_plan(
                        proposal=proposal,
                        market_analysis=market_analysis,
                        entry_price=entry_price,
                    )

                except ValueError:
                    if proposal.trade_style == "intraday":
                        execution_block_reason = "missing_15m_analysis"
                    else:
                        execution_block_reason = "missing_1h_analysis"

                else:
                    execution_request = BrokerExecutionRequest(
                        request_id=str(uuid4()),
                        agent_decision_id=proposal.agent_decision_id,
                        symbol=proposal.symbol,
                        direction=proposal.direction,
                        trade_style=proposal.trade_style,
                        confidence=proposal.confidence,
                        thesis=proposal.thesis,
                        volume_lots=TRADE_VOLUME_LOTS,
                        reference_price=entry_price,
                        stop_loss=plan.stop_loss,
                        take_profit=plan.take_profit,
                        max_holding_minutes=plan.max_holding_minutes,
                    )

                    if self.execution_persistence is not None:
                        self.execution_persistence.save_request(execution_request)

                    execution_result = self.broker_provider.open_position(
                        request=execution_request,
                    )

                    if self.execution_persistence is not None:
                        if execution_result.request_id != execution_request.request_id:
                            raise RuntimeError(
                                "Broker execution response request_id mismatch"
                            )

                        self.execution_persistence.update_result(execution_result)

        return AgentResult(
            decision_id=result.decision_id,
            decision=decision,
            proposal=proposal,
            approved=approved,
            position=position,
            execution_block_reason=execution_block_reason,
            execution_result=execution_result,
        )
