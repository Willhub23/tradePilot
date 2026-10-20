"""Descriptive, read-only diagnostics. No strategy runs, orders or profit inference."""
from collections import Counter
from math import isfinite
from statistics import fmean

from app.strategies.price_consistency import compare_prices, aware_time
from app.strategies.quick_trade_config import QuickTradeConfig


def obj(value):
    return value if isinstance(value, dict) else {}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def distribution(values):
    values = sorted(float(value) for value in values if number(value))
    def percentile(fraction):
        if not values: return None
        index = (len(values)-1)*fraction
        low = int(index)
        high = min(low+1, len(values)-1)
        return values[low]+(values[high]-values[low])*(index-low)
    return {"count":len(values), "min":values[0] if values else None,
            "mean":fmean(values) if values else None, "median":percentile(.5),
            "p90":percentile(.9), "p95":percentile(.95), "max":values[-1] if values else None}


def summarize_evaluations(rows, *, max_time_skew=5, meaningful_tp_fraction=.2):
    """One initial observation per persisted cycle; no revalidation double counting.

    Missing TP proposals never acquire fabricated targets. Statistical price pairs
    use receipt/event time proximity, which cannot confirm true event-time alignment.
    """
    rows = list(rows)
    limits = QuickTradeConfig()
    counts = Counter(dict(eligible=0, rejected=0, incomplete=0, blocked=0, error=0, not_evaluated=0))
    statuses, reasons, blocks, setups = Counter(), Counter(), Counter(), Counter()
    latencies = {name:[] for name in ("detection_ms", "strategy_evaluation_ms", "ai_reasoning_ms",
        "risk_validation_ms", "order_submission_ms", "submission_to_fill_confirmation_ms")}
    atr, ratios, pairs, unpaired = [], [], [], Counter()
    atr_above, atr_below, ratio_above, historical_limits_known = 0, 0, 0, 0
    evaluated_count = 0
    funnel = Counter(dict(raw_strategy_eligible=0, eligible_after_input_execution_gates=0,
        eligible_reached_ollama_confirmed=0, eligible_ollama_unknown=0, agent_stage_entered=0,
        execution_intents=0, broker_provider_calls=0, confirmed_submissions=0,
        confirmed_full_fills=0, confirmed_any_fills=0, execution_links_missing=0))
    for row in rows:
        data = obj(row.get("data"))
        report = obj(data.get("evaluation"))
        evaluation = obj(report.get("evaluation"))
        observed = obj(report.get("timeframe_observations"))
        evaluated_count += bool(evaluation)
        status = row.get("status", "unknown")
        statuses[status] += 1
        category = report.get("outcome")
        if not report:
            category = "blocked" if status in ("blocked", "skipped") else status
        category = {"incomplete_input":"incomplete", "started":"not_evaluated"}.get(category, category)
        counts[category if category in counts else "not_evaluated"] += 1
        setups[evaluation.get("setup_type") or ("not_identified" if evaluation else "not_evaluated")] += 1
        codes = {r.get("code") for key in ("rejection_reasons",) for r in (evaluation.get(key) or []) if isinstance(r, dict)}
        codes.update(r.get("code") for r in (report.get("input_failure_reasons") or []) if isinstance(r, dict))
        reasons.update(sorted(code for code in codes if code))
        if data.get("block_reason"): blocks[data["block_reason"]] += 1
        raw_limits = obj(data.get("strategy_config"))
        known = all(number(raw_limits.get(k)) for k in ("min_atr", "max_atr", "max_atr_ratio"))
        if known: historical_limits_known += 1
        used = raw_limits if known else limits.model_dump()
        a, ratio = observed.get("atr"), observed.get("atr_ratio")
        if number(a):
            atr.append(a)
            atr_above += a > used["max_atr"]
            atr_below += a < used["min_atr"]
        if number(ratio):
            ratios.append(ratio)
            ratio_above += ratio > used["max_atr_ratio"]
        timing = obj(data.get("timing"))
        for key in ("detection_ms", "strategy_evaluation_ms", "risk_validation_ms", "order_submission_ms"):
            value = timing.get(key)
            if value is None and key == "strategy_evaluation_ms": value = obj(report.get("timing")).get(key)
            if number(value) and value >= 0: latencies[key].append(value)
        reasoning = obj(timing.get("ai_reasoning"))
        if number(reasoning.get("duration_ms")) and reasoning["duration_ms"] >= 0:
            latencies["ai_reasoning_ms"].append(reasoning["duration_ms"])
        raw_eligible = evaluation.get("status") == "eligible"
        eligible = report.get("outcome") == "eligible"
        funnel["raw_strategy_eligible"] += raw_eligible
        funnel["eligible_after_input_execution_gates"] += eligible
        entered_agent = bool(timing.get("agent_stage_started_at"))
        funnel["agent_stage_entered"] += entered_agent
        if raw_eligible and reasoning.get("started_at"):
            funnel["eligible_reached_ollama_confirmed"] += 1
        elif raw_eligible and entered_agent and obj(row.get("agent_macro_context")).get("allow_new_trade") is not False:
            # Agent-stage entry or decision_id alone does not prove an Ollama call.
            funnel["eligible_ollama_unknown"] += 1
        if row.get("request_id"):
            funnel["execution_intents"] += 1
            execution_status = row.get("execution_status")
            if execution_status is None: funnel["execution_links_missing"] += 1
            submitted = execution_status in ("submitted", "pending", "filled") or bool(row.get("broker_order_id"))
            filled = execution_status == "filled"
            any_fill = filled or (number(row.get("filled_volume_lots")) and row["filled_volume_lots"] > 0)
            funnel["confirmed_submissions"] += submitted
            funnel["confirmed_full_fills"] += filled
            funnel["confirmed_any_fills"] += any_fill
            tracking = obj(obj(row.get("execution_request_data")).get("execution_tracking"))
            value = tracking.get("submission_to_fill_confirmation_ms")
            if number(value) and value >= 0: latencies["submission_to_fill_confirmation_ms"].append(value)
        funnel["broker_provider_calls"] += bool(timing.get("broker_call_started_at"))
        quote = obj(data.get("broker_snapshot"))
        proposal = obj(evaluation.get("proposal"))
        pair = compare_prices(report.get("reference_price"), quote.get("bid"), quote.get("ask"),
            evaluation.get("market_timestamp"), quote.get("observed_at"),
            target_distance=proposal.get("target_distance"), max_time_skew=max_time_skew)
        if pair["status"] == "comparable":
            pair["evaluation_id"] = row.get("id")
            evaluated = aware_time(evaluation.get("evaluated_at"))
            if evaluated is not None:
                pair["reference_receipt_age_at_evaluation_seconds"] = (evaluated-aware_time(pair["reference_timestamp"])).total_seconds()
                pair["broker_event_age_at_evaluation_seconds"] = (evaluated-aware_time(pair["broker_timestamp"])).total_seconds()
            pairs.append(pair)
        else:
            unpaired[pair["status"]] += 1
    with_tp = [p for p in pairs if p.get("actual_tp_fraction") is not None]
    comparable = len(pairs)
    return {
        "total_evaluations":len(rows), "counts":dict(counts), "final_statuses":dict(statuses),
        "rejections_by_code":dict(reasons), "block_reasons":dict(blocks), "setup_types":dict(setups),
        "count_semantics":"Initial gate outcomes; skipped controls count as blocked. Final cycle statuses are separate. Reason codes count once per cycle.",
        "atr": {"values":distribution(atr), "ratio":distribution(ratios),
            "above_max":atr_above, "below_min":atr_below, "ratio_above_max":ratio_above,
            "atr_observed":len(atr), "ratio_observed":len(ratios),
            "within_bounds":len(atr)-atr_above-atr_below,
            "above_max_rate":atr_above/len(atr) if atr else None,
            "below_min_rate":atr_below/len(atr) if atr else None,
            "ratio_above_max_rate":ratio_above/len(ratios) if ratios else None,
            "rows_with_persisted_limits":historical_limits_known,
            "rows_without_persisted_limits":len(rows)-historical_limits_known,
            "fallback_current_limits":{"min_atr":limits.min_atr,"max_atr":limits.max_atr,"max_atr_ratio":limits.max_atr_ratio},
            "limits_note":"Per-row persisted limits where available; otherwise current defaults, not proof of historical configuration."},
        "latency_ms":{name:distribution(values) for name,values in latencies.items()},
        "price_discrepancy":{
            "max_time_skew_seconds":max_time_skew, "meaningful_tp_fraction":meaningful_tp_fraction,
            "comparison_note":"Twelve Data receipt versus cTrader event timestamps; true market-event alignment is unknown.",
            "comparable_pairs":comparable, "unpaired_reasons":dict(unpaired),
            "absolute_mid_gap":distribution([p["absolute_mid_gap"] for p in pairs]),
            "pairs_with_actual_tp":len(with_tp), "pairs_without_actual_tp":comparable-len(with_tp),
            "exceeds_fraction_of_actual_tp":sum(p["actual_tp_fraction"] > meaningful_tp_fraction for p in with_tp),
            "actual_tp_exceedance_rate":sum(p["actual_tp_fraction"] > meaningful_tp_fraction for p in with_tp)/len(with_tp) if with_tp else None,
            "exceeds_fraction_of_current_min_target":sum(p["absolute_mid_gap"] > meaningful_tp_fraction*limits.min_target_distance for p in pairs),
            "exceeds_fraction_of_current_max_target":sum(p["absolute_mid_gap"] > meaningful_tp_fraction*limits.max_target_distance for p in pairs),
            "target_bounds_note":"Comparisons to configured $2-$5 bounds are context only; rejected setups have no inferred TP.",
            "pairs":pairs},
        "funnel":dict(funnel),
        "sample_assessment":{
            "market_evaluations":evaluated_count,
            "note":"Insufficient observations for trends" if len(atr)<30 else "Descriptive observations only; no significance or profitability inference performed",
            "trend_inference":"not_performed", "missed_profitable_trades":"not_inferred"},
    }


def load_evaluations(engine, *, since=None, until=None):
    """Repeatable, bounded read-only transaction; never opens a broker connection."""
    from sqlalchemy import select
    from app.db_models import StrategyEvaluationRecord as S, BrokerExecutionRecord as B, AgentDecisionRecord as A
    dialect = engine.dialect.name
    if dialect not in ("postgresql", "sqlite"):
        raise ValueError("Read-only loading supports PostgreSQL and isolated SQLite only")
    query = select(S.id,S.opportunity_id,S.strategy_id,S.symbol,S.created_at,S.updated_at,S.status,
        S.agent_decision_id,S.request_id,S.data,B.status.label("execution_status"),
        B.request_data.label("execution_request_data"),B.broker_order_id,B.broker_position_id,
        B.filled_volume_lots,B.fill_price,A.macro_context.label("agent_macro_context")).outerjoin(
        B,S.request_id==B.request_id).outerjoin(A,S.agent_decision_id==A.id).order_by(S.created_at,S.id)
    if since is not None: query = query.where(S.created_at >= since)
    if until is not None: query = query.where(S.created_at < until)
    with engine.connect() as connection:
        if dialect == "postgresql": connection = connection.execution_options(isolation_level="REPEATABLE READ")
        with connection.begin():
            if dialect == "postgresql":
                connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                connection.exec_driver_sql("SET LOCAL statement_timeout = '15s'")
            else:
                connection.exec_driver_sql("PRAGMA query_only=ON")
            return [dict(row) for row in connection.execute(query).mappings()]
