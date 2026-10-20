from datetime import datetime, timezone
import logging
logger = logging.getLogger(__name__)
from uuid import uuid4
import threading
from app.config.strategy_config import StrategySelection

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
        strategy_selection: StrategySelection | None = None,
    ):
        self.runtime = None
        self.strategy_selection = strategy_selection or StrategySelection.from_env()
        self._cycle_lock = threading.Lock()
        self.quick_trade_cycle = None
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

    def exposure_block_reason(self):
        if self.broker_provider.get_open_positions():
            return "position_already_open"
        if self.execution_persistence is not None and self.execution_persistence.get_unresolved():
            return "unresolved_broker_execution"
        client = getattr(self.broker_provider, "client", None)
        if (self.execution_persistence is not None and client is not None
                and self.execution_persistence.get_unresolved_closes(client.account_id)):
            return "unresolved_position_close"
        return None

    def entry_block_reason(self):
        reason = self.runtime.block_reason() if self.runtime is not None else None
        if reason: return reason
        client = getattr(self.broker_provider, "client", None)
        if (client is None or client.environment != "demo" or not client.authenticated or not client.demo_verified):
            return "broker_not_verified_demo"
        return self.exposure_block_reason()

    def run(self, symbol: str) -> AgentResult:
        if not self._cycle_lock.acquire(blocking=False):
            raise RuntimeError("A trading cycle is already running")
        try:
            if self.strategy_selection.observation_enabled or self.strategy_selection.strategy == "quick_trade":
                if self.quick_trade_cycle is None:
                    from app.services.quick_trade_cycle_service import QuickTradeCycleService
                    self.quick_trade_cycle = QuickTradeCycleService(self)
                if self.strategy_selection.observation_enabled:
                    return self.quick_trade_cycle.observe(symbol)
                # A runtime started read-only cannot fall through into inference.
                if self.runtime is not None and self.runtime.observation_enabled is True:
                    raise RuntimeError("Observation runtime/configuration mismatch")
                return self.quick_trade_cycle.run(symbol)
            if self.runtime is not None:
                if self.runtime.observation_enabled is True:
                    raise RuntimeError("Observation runtime/configuration mismatch")
                if symbol.upper() != "XAUUSD":
                    raise ValueError("Only XAUUSD is supported by the DEMO runtime")
                reason = self.exposure_block_reason()
                if reason:
                    from app.models.agent_models import AgentDecision
                    return AgentResult(decision_id=None, decision=AgentDecision(action="hold", trade_style="none",
                        confidence=0, thesis=reason), proposal=None, approved=False, execution_block_reason=reason)
            # The provider serializes final recovery/entry checks; inference holds no runtime lock.
            return self._run(symbol)
        finally:
            self._cycle_lock.release()

    def _run(self, symbol):
        logger.info("Trading cycle started symbol=%s timestamp=%s", symbol, datetime.now(timezone.utc).isoformat())
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

        logger.info("Agent market context %s", context.model_dump_json())

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

        if self.runtime is not None and self.runtime.block_reason():
            execution_block_reason = self.runtime.block_reason()
        elif approved and proposal is not None:
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

                    logger.info("Broker request %s", execution_request.model_dump_json())
                    execution_result = self.broker_provider.open_position(
                        request=execution_request,
                    )

                    if self.execution_persistence is not None:
                        if execution_result.request_id != execution_request.request_id:
                            raise RuntimeError(
                                "Broker execution response request_id mismatch"
                            )

                        self.execution_persistence.update_result(execution_result)

        logger.info("Trading cycle completed decision_id=%s approved=%s block=%s result=%s",
                    result.decision_id, approved, execution_block_reason,
                    execution_result.model_dump_json() if execution_result else None)
        return AgentResult(
            decision_id=result.decision_id,
            decision=decision,
            proposal=proposal,
            approved=approved,
            position=position,
            execution_block_reason=execution_block_reason,
            execution_result=execution_result,
        )
