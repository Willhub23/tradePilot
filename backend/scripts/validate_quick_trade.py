"""Read-only Quick Trade validation. Run with python -m scripts.validate_quick_trade."""
import argparse
from collections import deque
from datetime import datetime, timezone
import json
from pathlib import Path

import httpx
from pydantic import ValidationError

from app.models.market_models import MultiTimeframeAnalysis
from app.strategies.models import ExecutionEvidence
from app.strategies.input_adapter import BrokerEvidenceSnapshot, adapt_strategy_input
from app.strategies.validation import validate_snapshot


def captured_inputs(path, limit):
    """Accept full StrategyInput JSON, market-analysis JSON, or existing runtime logs.

    Log replay uses the UTC log capture timestamp for freshness checks. Plain market
    snapshots use their quote timestamp; an explicit evaluated_at takes precedence.
    """
    text = Path(path).read_text()
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        rows = deque(maxlen=limit)
        for line in text.splitlines():
            if "Agent market context " in line:
                prefix, payload = line.split("Agent market context ", 1)
                stamp = datetime.strptime(prefix[:24], "%Y-%m-%d %H:%M:%S,%fZ").replace(tzinfo=timezone.utc)
                rows.append({"analysis": json.loads(payload)["market_analysis"], "evaluated_at": stamp, "market_source": "twelve_data"})
            elif line.lstrip().startswith("{"):
                rows.append(json.loads(line))
        yield from rows
    else:
        yield from (raw if isinstance(raw, list) else [raw])[-limit:]


def evaluate_raw(raw, source, *, replay, execution=None, broker_snapshot=None, market_source=None):
    try:
        analysis = raw.get("analysis", raw.get("market_analysis", raw))
        raw_execution = execution if execution is not None else raw.get("execution")
        raw_broker = broker_snapshot if broker_snapshot is not None else raw.get("broker_snapshot")
        broker = BrokerEvidenceSnapshot.model_validate(raw_broker) if raw_broker is not None else None
        data = adapt_strategy_input(
            MultiTimeframeAnalysis.model_validate(analysis),
            evaluated_at=datetime.fromisoformat(str(raw.get("evaluated_at", analysis.get("price_timestamp"))).replace("Z", "+00:00")) if replay else datetime.now(timezone.utc),
            execution=ExecutionEvidence.model_validate(raw_execution) if raw_execution is not None else None,
            broker_snapshot=broker, market_source=market_source or raw.get("market_source"),
        )
        report = validate_snapshot(data, source=source, replay=replay).model_dump(mode="json")
        report["market_source"] = market_source or raw.get("market_source")
        report["broker_observations"] = broker.model_dump(mode="json") if broker else None
        return report
    except (ValidationError, ValueError, TypeError, AttributeError) as exc:
        return {"source": source, "replay": replay, "outcome": "invalid_input", "input_error": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", action="store_true", help="GET existing local market-analysis endpoint only")
    parser.add_argument("--market-url", default="http://127.0.0.1:8000/api/market/XAUUSD/multi-timeframe")
    parser.add_argument("--snapshots", action="append", default=[], help="Captured full JSON/JSONL or runtime.log")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--execution-evidence", help="Optional genuine ExecutionEvidence JSON; timestamps are checked")
    parser.add_argument("--broker-snapshot", help="Captured BrokerEvidenceSnapshot JSON, retaining original observation time")
    parser.add_argument("--market-source", choices=["twelve_data"], help="Explicit UTC provenance for captured Twelve Data candles")
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    if not args.current and not args.snapshots:
        parser.error("select --current and/or --snapshots")
    execution = None
    if args.execution_evidence:
        execution = ExecutionEvidence.model_validate_json(Path(args.execution_evidence).read_text()).model_dump(mode="json")
    if args.execution_evidence and args.broker_snapshot:
        parser.error("choose --execution-evidence or --broker-snapshot")
    broker_snapshot = None
    if args.broker_snapshot:
        broker_snapshot = BrokerEvidenceSnapshot.model_validate_json(Path(args.broker_snapshot).read_text()).model_dump(mode="json")
    reports = []
    if args.current:
        try:
            response = httpx.get(args.market_url, timeout=90)
            response.raise_for_status()
            reports.append(evaluate_raw(response.json(), args.market_url, replay=False, execution=execution, broker_snapshot=broker_snapshot, market_source="twelve_data"))
        except (httpx.HTTPError, ValueError) as exc:
            reports.append({"source": args.market_url, "outcome": "data_unavailable", "error": str(exc)})
    for path in args.snapshots:
        try:
            for index, raw in enumerate(captured_inputs(path, args.limit)):
                reports.append(evaluate_raw(raw, f"{path}#{index + 1}", replay=True, execution=execution, broker_snapshot=broker_snapshot, market_source=args.market_source))
        except (OSError, ValueError, KeyError) as exc:
            reports.append({"source": path, "outcome": "invalid_input", "input_error": str(exc)})
    print(json.dumps(reports, indent=2))
    return reports


if __name__ == "__main__":
    main()
