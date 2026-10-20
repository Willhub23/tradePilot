"""One controlled read-only XAUUSD DEMO evaluation, without FastAPI startup."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from time import perf_counter_ns
from types import SimpleNamespace

from dotenv import load_dotenv

from app.strategies.input_adapter import adapt_strategy_input, collect_broker_snapshot
from app.strategies.validation import validate_snapshot
from app.strategies.validation_costs import ValidationCostConfig, verified_commission_estimate


def build_analysis_service():
    # Same dependencies as market_routes; import neither API startup nor agent code.
    from app.providers.twelve_data_provider import TwelveDataProvider
    from app.services.market_service import MarketService
    from app.services.analysis_service import AnalysisService
    from app.services.indicator_service import IndicatorService
    from app.services.signal_service import SignalService
    from app.services.market_structure_service import MarketStructureService
    from app.services.volatility_service import VolatilityService
    from app.services.market_regime_service import MarketRegimeService
    from app.services.moving_average_service import MovingAverageService
    from app.services.price_action_service import PriceActionService
    key = os.getenv("TWELVE_DATA_API_KEY")
    if not key:
        raise ValueError("TWELVE_DATA_API_KEY is not configured")
    indicators = IndicatorService()
    return AnalysisService(MarketService(TwelveDataProvider(key)), indicators, SignalService(),
                           MarketStructureService(), VolatilityService(), MarketRegimeService(),
                           MovingAverageService(indicators), PriceActionService())


def build_broker():
    from app.integrations.ctrader.client import CTraderClient
    from app.providers.ctrader_broker_provider import CTraderBrokerProvider
    from ctrader_open_api.messages import OpenApiMessages_pb2 as M
    # Unsolicited execution events must not cause DB writes or position management.
    client = CTraderClient(execution_event_handler=SimpleNamespace(process=lambda event, **kwargs: None))
    client.order_guard = lambda: False
    client.close_guard = lambda: False
    original_send = client.send
    allowed = (M.ProtoOASymbolsListReq, M.ProtoOASymbolByIdReq, M.ProtoOAGetTickDataReq, M.ProtoOAAssetListReq)
    def read_only_send(request, **kwargs):
        if not isinstance(request, allowed):
            raise ValueError("Read-only validation forbids this broker request")
        return original_send(request, **kwargs)
    client.send = read_only_send
    return CTraderBrokerProvider(client, persistence=object())


def run_validation(cost_config, *, analysis_service=None, provider=None, auth_timeout=30):
    """Owns the supplied/factory DEMO client for this single run; always stops it."""
    total_start = perf_counter_ns()
    report = {"symbol": "XAUUSD", "mode": "read_only_demo", "orders_submitted": False,
              "costs": {"slippage_allowance": cost_config.slippage_allowance,
                        "slippage_allowance_units": "round-trip XAUUSD price distance",
                        "realized_slippage": None},
              "latency_ms": {"market_data": None, "broker_evidence": None,
                             "strategy_evaluation": None, "total": None},
              "errors": [], "disconnected": False}
    analysis = None
    market_start = perf_counter_ns()
    try:
        analysis = (analysis_service or build_analysis_service()).analyze_multi_timeframe("XAUUSD")
        report["market_analysis"] = analysis.model_dump(mode="json")
    except Exception as exc:
        # Do not expose URLs/query strings (API keys) from HTTP exception messages.
        report["errors"].append({"stage": "market_data", "type": type(exc).__name__,
                                 "message": "Market analysis retrieval failed; verify Twelve Data availability/key/quota"})
    finally:
        report["latency_ms"]["market_data"] = (perf_counter_ns() - market_start) / 1e6
    broker_start = perf_counter_ns()
    snapshot = None
    client = None
    try:
        provider = provider or build_broker()
        client = provider.client
        if client.environment != "demo":
            raise ValueError("DEMO environment required")
        client.start()
        if not client.wait_until_authenticated(auth_timeout) or not client.authenticated or not client.demo_verified:
            raise RuntimeError("Configured DEMO account could not be authenticated/verified before timeout")
        report["account"] = {"account_id": client.account_id, "demo_verified": client.demo_verified}
        snapshot = collect_broker_snapshot(provider, as_of=datetime.now(timezone.utc))
        metadata = provider.get_symbol_metadata("XAUUSD")
        report["symbol_metadata"] = {name: getattr(metadata, name) if metadata.HasField(name) else None
                                     for name in ("symbolId", "digits", "pipPosition", "slDistance", "tpDistance", "distanceSetIn", "lotSize")}
        from app.config.trading_config import TRADE_VOLUME_LOTS
        commission = verified_commission_estimate(provider,
            reference_price=analysis.price if analysis else 1, volume_lots=TRADE_VOLUME_LOTS)
        report["symbol_metadata"]["quote_currency"] = "USD"
        report["costs"]["commission"] = commission
        report["costs"]["volume_lots_for_estimation"] = TRADE_VOLUME_LOTS
        if analysis is not None and commission["round_trip_price_cost"] is not None:
            additional = float(Decimal(str(commission["round_trip_price_cost"])) + Decimal(str(cost_config.slippage_allowance)))
            snapshot = snapshot.model_copy(update={"additional_round_trip_cost": additional,
                "unavailable": [s for s in snapshot.unavailable if not s.startswith("additional_round_trip_cost:")]})
        report["broker_snapshot"] = snapshot.model_dump(mode="json")
    except Exception as exc:
        report["errors"].append({"stage": "broker_evidence", "type": type(exc).__name__,
            "message": str(exc) if type(exc) in (ValueError, RuntimeError, TimeoutError) else "DEMO transport request failed",
            "authentication_verified": bool(client and client.authenticated and client.demo_verified)})
    finally:
        report["latency_ms"]["broker_evidence"] = (perf_counter_ns() - broker_start) / 1e6
        if client is not None:
            try:
                client.stop()
                report["disconnected"] = True
            except Exception as exc:
                report["errors"].append({"stage": "disconnect", "type": type(exc).__name__})
    if analysis is not None:
        try:
            data = adapt_strategy_input(analysis, evaluated_at=datetime.now(timezone.utc),
                                        broker_snapshot=snapshot, market_source="twelve_data")
            result = validate_snapshot(data, source="standalone_current_twelve_data_ctrader_demo")
            report["validation"] = result.model_dump(mode="json")
            report["evaluated_at"] = data.evaluated_at.isoformat()
            report["market_source"] = "twelve_data"
            report["directional_bias"] = analysis.overall_bias
            report["latency_ms"]["strategy_evaluation"] = result.timing.strategy_evaluation_ms
        except (ValueError, TypeError) as exc:
            report["errors"].append({"stage": "validation_input", "type": type(exc).__name__, "message": str(exc)})
    report["outcome"] = report.get("validation", {}).get("outcome", "data_unavailable")
    report["latency_ms"]["total"] = (perf_counter_ns() - total_start) / 1e6
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--slippage-allowance", type=float, required=True,
                        help="Explicit pre-trade round-trip allowance in XAUUSD price units; not measured slippage")
    parser.add_argument("--auth-timeout", type=float, default=30)
    args = parser.parse_args(argv)
    if args.auth_timeout <= 0 or args.auth_timeout > 120:
        parser.error("--auth-timeout must be in (0, 120]")
    config = ValidationCostConfig(slippage_allowance=args.slippage_allowance)
    load_dotenv(args.env_file)
    report = run_validation(config, auth_timeout=args.auth_timeout)
    print(json.dumps(report, indent=2))
    return report


if __name__ == "__main__":
    main()
