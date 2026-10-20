"""Opt-in Quick Trade orchestration using the existing AI, risk and broker pipeline."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from time import perf_counter_ns
from uuid import uuid4

from app.config.trading_config import TRADE_VOLUME_LOTS
from app.models.agent_models import AgentDecision, AgentMarketContext, AgentResult
from app.models.execution_models import BrokerExecutionRequest, QuickTradeExecutionGuard
from app.strategies.execution_validation import validate_preserved_levels
from app.strategies.price_consistency import compare_prices
from app.strategies.models import StrategyRejection
from app.strategies.quick_trade_config import QuickTradeConfig
from app.services.execution_timing import PROCESS_ID
from app.services.strategy_evaluation_persistence_service import StrategyEvaluationPersistenceService
from app.strategies.input_adapter import adapt_strategy_input, collect_broker_snapshot
from app.strategies.validation import validate_snapshot
from app.strategies.validation_costs import verified_commission_estimate


def now():
    return datetime.now(timezone.utc)


class QuickTradeCycleService:
    def __init__(self, cycle, persistence=None):
        self.cycle = cycle
        self.persistence = persistence or StrategyEvaluationPersistenceService()

    def _observe(self):
        c = self.cycle
        analysis = c.analysis_service.analyze_multi_timeframe(symbol="XAUUSD")
        snapshot = collect_broker_snapshot(c.broker_provider, as_of=now())
        costs = verified_commission_estimate(c.broker_provider, reference_price=analysis.price,
                                              volume_lots=TRADE_VOLUME_LOTS)
        if costs["round_trip_price_cost"] is not None:
            total = float(Decimal(str(costs["round_trip_price_cost"])) + Decimal(str(c.strategy_selection.slippage_allowance)))
            snapshot = snapshot.model_copy(update={"additional_round_trip_cost": total,
                "unavailable": [s for s in snapshot.unavailable if not s.startswith("additional_round_trip_cost:")]})
        data = adapt_strategy_input(analysis, evaluated_at=now(), broker_snapshot=snapshot, market_source="twelve_data")
        report = validate_snapshot(data, source="autonomous_quick_trade")
        candidate = report.evaluation.proposal
        comparison = compare_prices(analysis.price, snapshot.bid, snapshot.ask,
            analysis.price_timestamp, snapshot.observed_at,
            target_distance=candidate.target_distance if candidate else None)
        report.price_consistency = comparison
        # Reuse the existing execution drift limit before inference, not a new entry rule.
        if report.outcome == "eligible":
            executable = snapshot.ask if candidate.direction == "long" else snapshot.bid
            gap = abs(Decimal(str(executable))-Decimal(str(candidate.entry_reference)))
            if gap > Decimal(str(c.strategy_selection.max_price_drift)):
                report.outcome = "blocked"
                report.input_failure_reasons.append(StrategyRejection(
                    code="analysis_broker_price_discrepancy",
                    message="Analysis reference differs from the authoritative executable broker quote beyond the existing drift limit",
                    measured=float(gap), limit=c.strategy_selection.max_price_drift))
        return data, snapshot, costs, report

    def observe(self, symbol):
        """Persist a read-only evaluation. Never enter the AI/execution pipeline."""
        c = self.cycle
        record_id = str(uuid4())
        started = perf_counter_ns()
        audit = {"mode": "observation", "orders_enabled": False, "ollama_invoked": False,
                 "cycle_started_at": now().isoformat(), "timing": {},
                 "slippage_allowance": c.strategy_selection.slippage_allowance,
                 "realized_slippage": None,
                 "strategy_config": QuickTradeConfig().model_dump(mode="json"),
                 "execution_guard_config": c.strategy_selection.model_dump(mode="json")}
        status, reason, opportunity_id = "started", None, None
        self.persistence.save(record_id, status=status, data=audit)
        try:
            runtime = c.runtime
            if symbol.upper() != "XAUUSD":
                reason = "unsupported_symbol"
            elif runtime is None or runtime.observation_enabled is not True:
                reason = "observation_requires_read_only_demo_runtime"
            else:
                reason = runtime.observation_block_reason()
                audit["entries_paused"] = runtime.paused
                if reason is None:
                    runtime.store.assert_owner()
            if reason is None and not c.market_session_service.is_market_open():
                reason = "market_closed"
            if reason:
                status = "blocked"
            else:
                generation = runtime.client.generation
                epoch = runtime.safety_epoch
                detection_start = perf_counter_ns()
                data, snapshot, costs, report = self._observe()
                report.source = "quick_trade_observation"
                audit.update(evaluation=report.model_dump(mode="json"), costs=costs,
                             broker_snapshot=snapshot.model_dump(mode="json"))
                audit["timing"].update(detection_ms=(perf_counter_ns()-detection_start)/1e6,
                                      strategy_evaluation_ms=report.timing.strategy_evaluation_ms)
                opportunity_id, status = report.opportunity_id, report.outcome
                reason = runtime.observation_block_reason()
                if not reason and (generation != runtime.client.generation or epoch != runtime.safety_epoch):
                    reason = "runtime_changed_during_observation"
                runtime.store.assert_owner()
                if reason:
                    status = "blocked"
        except Exception as exc:
            status, reason = "incomplete_input", "quick_trade_evidence_unavailable"
            audit["input_error"] = type(exc).__name__
        audit.update(block_reason=reason, cycle_completed_at=now().isoformat())
        audit["timing"]["total_observation_ms"] = (perf_counter_ns()-started)/1e6
        self.persistence.save(record_id, status=status, data=audit, opportunity_id=opportunity_id)
        return AgentResult(decision_id=None,
            decision=AgentDecision(action="hold", trade_style="none", confidence=0,
                                   thesis=reason or "Read-only Quick Trade observation: " + status),
            proposal=None, approved=False, execution_block_reason=reason or "observation_only",
            strategy_evaluation_id=record_id, opportunity_id=opportunity_id)

    def run(self, symbol):
        c = self.cycle
        record_id = str(uuid4())
        audit = {"cycle_started_at": now().isoformat(), "slippage_allowance": c.strategy_selection.slippage_allowance,
                 "realized_slippage": None, "timing": {},
                 "strategy_config": QuickTradeConfig().model_dump(mode="json"),
                 "execution_guard_config": c.strategy_selection.model_dump(mode="json")}
        status, opportunity_id, decision_id, request_id = "started", None, None, None
        def save():
            self.persistence.save(record_id, status=status, data=audit, opportunity_id=opportunity_id,
                                  decision_id=decision_id, request_id=request_id)
        def held(reason, decision=None, approved=False):
            audit["block_reason"] = reason
            audit["cycle_completed_at"] = now().isoformat()
            save()
            return AgentResult(decision_id=decision_id,
                decision=decision or AgentDecision(action="hold", trade_style="none", confidence=0, thesis=reason),
                proposal=None, approved=approved, execution_block_reason=reason,
                strategy_evaluation_id=record_id, opportunity_id=opportunity_id)
        save()  # Persistence must work before inference or submission.
        try:
            if symbol.upper() != "XAUUSD":
                status = "blocked"
                return held("unsupported_symbol")
            if c.runtime is None or c.execution_persistence is None:
                status = "blocked"
                return held("quick_trade_requires_demo_runtime")
            reason = c.entry_block_reason()
            if reason:
                status = "skipped"
                return held(reason)
            if not c.market_session_service.is_market_open():
                status = "skipped"
                return held("market_closed")
            epoch, generation = c.runtime.safety_epoch, c.broker_provider.client.generation
            audit["timing"]["detection_started_at"] = now().isoformat()
            detection_start = perf_counter_ns()
            try:
                data, snapshot, costs, report = self._observe()
            except Exception as exc:
                status = "incomplete_input"
                audit["input_error"] = type(exc).__name__
                return held("quick_trade_evidence_unavailable")
            detected_ns = perf_counter_ns()
            opportunity_id = report.opportunity_id
            status = report.outcome
            audit.update(evaluation=report.model_dump(mode="json"), costs=costs,
                         broker_snapshot=snapshot.model_dump(mode="json"))
            audit["timing"].update(detection_ms=(detected_ns-detection_start)/1e6,
                                  strategy_evaluation_ms=report.timing.strategy_evaluation_ms)
            save()
            if report.outcome != "eligible":
                return held("quick_trade_" + report.outcome)
            candidate = report.evaluation.proposal
            # Recheck occupancy/pause before spending inference time.
            reason = c.entry_block_reason()
            if reason:
                status = "blocked"
                return held(reason)
            macro = c.macro_context_service.build_context(events=c.macro_service.get_events(), now=now())
            context = AgentMarketContext(symbol="XAUUSD", market_analysis=data.analysis, macro_context=macro,
                evidence_priority=c.evidence_priority_service.prioritize(macro_context=macro),
                strategy_evaluation=report.evaluation, opportunity_id=opportunity_id)
            reason = c.entry_block_reason()
            if reason:
                status = "blocked"
                return held(reason)
            audit["timing"]["agent_stage_started_at"] = now().isoformat()
            start = perf_counter_ns()
            result = c.agent_service.make_decision(context)  # NO runtime operation lock during inference.
            audit["timing"].update(agent_stage_completed_at=now().isoformat(), ai_decision_stage_ms=(perf_counter_ns()-start)/1e6)
            if result.reasoning_timing is not None:
                audit["timing"]["ai_reasoning"] = result.reasoning_timing
            decision_id = result.decision_id
            decision = result.decision
            save()
            if decision.action != candidate.direction or decision.trade_style != "intraday":
                status = "ai_rejected"
                return held("ai_did_not_approve_quick_proposal", decision)
            proposal = c.trade_proposal_service.create_proposal(context, decision, decision_id)
            start = perf_counter_ns()
            approved = c.risk_service.approve(proposal)
            audit["timing"].update(risk_validated_at=now().isoformat(), risk_validation_ms=(perf_counter_ns()-start)/1e6)
            if not approved:
                status = "risk_rejected"
                return held("risk_rejected", decision)
            reason = c.entry_block_reason()
            if reason:
                status = "blocked"
                return held(reason, decision)
            # Refresh all evidence after AI. Preserve the original proposal, never substitute a new one.
            fresh, fresh_snapshot, fresh_costs, fresh_report = self._observe()
            fresh_report = fresh_report.model_copy(update={"opportunity_id": opportunity_id,
                                                         "detected_at": report.detected_at})
            audit["pre_submission"] = {"evaluation": fresh_report.model_dump(mode="json"),
                "broker_snapshot": fresh_snapshot.model_dump(mode="json"), "costs": fresh_costs}
            if fresh_report.outcome != "eligible":
                status = "expired"
                return held("price_drift_exceeded" if any(
                    r.code == "analysis_broker_price_discrepancy" for r in fresh_report.input_failure_reasons)
                    else "strategy_no_longer_eligible", decision)
            new = fresh_report.evaluation.proposal
            config = c.strategy_selection
            entry = fresh_snapshot.ask if candidate.direction == "long" else fresh_snapshot.bid
            side = 1 if candidate.direction == "long" else -1
            tick = float(c.broker_provider.price_tick(c.broker_provider.get_symbol_metadata("XAUUSD")))
            if (new.direction != candidate.direction or fresh_report.evaluation.setup_type != report.evaluation.setup_type
                    or abs(fresh_report.evaluation.invalidation_level - report.evaluation.invalidation_level) > tick
                    or side*(candidate.take_profit-new.take_profit) > tick):
                status = "expired"
                return held("strategy_setup_changed", decision)
            if max(abs(entry-candidate.entry_reference), abs(fresh.analysis.price-candidate.entry_reference)) > config.max_price_drift:
                status = "expired"
                return held("price_drift_exceeded", decision)
            try:
                validate_preserved_levels(direction=candidate.direction, entry=entry,
                    original_entry=candidate.entry_reference, stop_loss=candidate.stop_loss,
                    take_profit=candidate.take_profit, stop_budget=candidate.stop_distance,
                    snapshot=fresh_snapshot, additional_cost=fresh.execution.additional_round_trip_cost,
                    max_price_drift=config.max_price_drift)
            except ValueError as exc:
                status = "expired"
                return held(str(exc), decision)
            final_macro = c.macro_context_service.build_context(events=c.macro_service.get_events(), now=now())
            audit["pre_submission"]["macro_context"] = final_macro
            if not final_macro["allow_new_trade"]:
                status = "blocked"
                return held("macro_blocks_new_trade", decision)
            with c.runtime.operation():
                reason = c.entry_block_reason()
                if not reason and (c.runtime.safety_epoch != epoch or c.broker_provider.client.generation != generation):
                    reason = "runtime_changed_during_reasoning"
                age = (perf_counter_ns()-detected_ns)/1e9
                if not reason and age > config.max_opportunity_age_seconds: reason = "opportunity_expired"
                if not reason and not c.market_session_service.is_market_open(): reason = "market_closed"
                if not reason and (not c.market_session_service.is_quote_fresh(fresh.analysis.price_timestamp)
                        or not c.market_session_service.is_quote_fresh(fresh.execution.observed_at)):
                    reason = "stale_quote"
                if reason:
                    status = "expired"
                    return held(reason, decision)
                start = perf_counter_ns()
                if not c.risk_service.approve(proposal):
                    status = "risk_rejected"
                    return held("risk_rejected", decision)
                audit["timing"]["final_risk_validation_ms"] = (perf_counter_ns()-start)/1e6
                submitted_ns = perf_counter_ns()
                submitted_at = now()
                remaining = config.max_opportunity_age_seconds-age
                request = BrokerExecutionRequest(request_id=str(uuid4()), agent_decision_id=decision_id,
                    opportunity_id=opportunity_id, strategy_id=report.evaluation.strategy_id,
                    quick_trade_guard=QuickTradeExecutionGuard(original_entry=candidate.entry_reference,
                        stop_budget=candidate.stop_distance, max_price_drift=config.max_price_drift,
                        additional_cost=fresh.execution.additional_round_trip_cost),
                    symbol="XAUUSD", direction=candidate.direction, volume_lots=TRADE_VOLUME_LOTS,
                    reference_price=entry, stop_loss=candidate.stop_loss, take_profit=candidate.take_profit,
                    max_holding_minutes=candidate.max_holding_minutes, trade_style="intraday",
                    confidence=decision.confidence, thesis=decision.thesis,
                    valid_until=min(submitted_at+timedelta(seconds=remaining),
                                    fresh.execution.observed_at+timedelta(seconds=60),
                                    fresh.analysis.price_timestamp+timedelta(seconds=60)),
                    timing={"process_id": PROCESS_ID, "submission_monotonic_ns": submitted_ns,
                            "submission_started_at": submitted_at.isoformat()})
                c.broker_provider.relative_protection_units(request, c.broker_provider.get_symbol_metadata("XAUUSD"))
                c.execution_persistence.save_request(request)
                request_id = request.request_id
                status = "submitting"
                save()  # Durable linkage before the broker can receive a request.
                broker_call_ns = perf_counter_ns()
                audit["timing"]["broker_call_started_at"] = now().isoformat()
                execution = c.broker_provider.open_position(request=request)
                audit["timing"]["broker_call_ms"] = (perf_counter_ns()-broker_call_ns)/1e6
                audit["timing"].update(order_submission_ms=(perf_counter_ns()-submitted_ns)/1e6,
                    submission_returned_at=now().isoformat())
                if execution.request_id != request_id:
                    raise RuntimeError("Broker execution response request_id mismatch")
                c.execution_persistence.update_result(execution)
                status = execution.status
                audit["execution_result"] = execution.model_dump(mode="json")
                audit["cycle_completed_at"] = now().isoformat()
                save()
            return AgentResult(decision_id=decision_id, decision=decision, proposal=proposal,
                approved=True, execution_result=execution, strategy_evaluation_id=record_id, opportunity_id=opportunity_id)
        except Exception as exc:
            status = "error"
            audit["error"] = type(exc).__name__  # No credential-bearing transport URLs.
            save()
            raise
