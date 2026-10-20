"""Read-only conversion of existing analysis and cTrader observations.

No connection is started here. Call broker collection only from a worker thread,
with an existing authenticated DEMO provider. Unknown costs remain unknown.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.models.market_models import MultiTimeframeAnalysis
from app.strategies.models import ExecutionEvidence, StrategyInput


def utc(timestamp: datetime, *, known_utc: bool = False) -> datetime:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        if not known_utc:
            return timestamp  # Unknown timezone must remain an explicit input gap.
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


class BrokerEvidenceSnapshot(BaseModel):
    """Price units, not lots or money. Retains the broker's observation timestamp.

    A snapshot without a two-sided quote can still supply verified constraints.
    Missing optional protobuf constraints are kept unknown, never defaulted to zero.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    symbol: Literal["XAUUSD"] = "XAUUSD"
    source: str = Field(min_length=1)
    observed_at: AwareDatetime | None = None
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    digits: int | None = Field(default=None, ge=0, le=18)
    min_stop_distance: float | None = Field(default=None, ge=0)
    min_target_distance: float | None = Field(default=None, ge=0)
    additional_round_trip_cost: float | None = Field(default=None, ge=0)
    unavailable: list[str] = Field(default_factory=list)


def adapt_strategy_input(
    analysis: MultiTimeframeAnalysis | None,
    *,
    evaluated_at: datetime,
    execution: ExecutionEvidence | None = None,
    broker_snapshot: BrokerEvidenceSnapshot | None = None,
    market_source: str | None = None
) -> StrategyInput:
    """Normalize known Twelve Data UTC candles, preserving times and all prices.

    Unknown naive quote timestamps stay unknown. No current time is substituted
    for a quote, execution observation, or historical capture timestamp.
    """
    if analysis is not None:
        if analysis.symbol != "XAUUSD":
            raise ValueError("Quick Trade input adapter supports XAUUSD only")
        analysis = analysis.model_copy(deep=True)
        analysis.price_timestamp = utc(analysis.price_timestamp)
        for tf in analysis.timeframes:
            tf.latest_candle_timestamp = utc(
                tf.latest_candle_timestamp, known_utc=market_source == "twelve_data"
            )
    if execution is not None and broker_snapshot is not None:
        raise ValueError("Supply execution evidence or a broker snapshot, not both")
    if broker_snapshot is not None and broker_snapshot.observed_at is not None:
        snapshot = broker_snapshot
        spread = None
        if snapshot.bid is not None and snapshot.ask is not None:
            if snapshot.ask < snapshot.bid:
                raise ValueError("Broker ask must be greater than or equal to bid")
            spread = float(Decimal(str(snapshot.ask)) - Decimal(str(snapshot.bid)))
        execution = ExecutionEvidence(
            observed_at=utc(snapshot.observed_at),
            source=snapshot.source,
            spread=spread,
            additional_round_trip_cost=snapshot.additional_round_trip_cost,
            min_stop_distance=snapshot.min_stop_distance,
            min_target_distance=snapshot.min_target_distance,
        )
    elif execution is not None:
        execution = execution.model_copy(
            update={"observed_at": utc(execution.observed_at)}
        )
    return StrategyInput(
        analysis=analysis, execution=execution, evaluated_at=utc(evaluated_at)
    )


def collect_broker_snapshot(
    provider, *, as_of: datetime, spot_event=None
) -> BrokerEvidenceSnapshot:
    """Reuse provider metadata and send() for read-only observations only.

    Prefer a decoded two-sided ProtoOASpotEvent when supplied. Otherwise request
    recent BID/ASK ticks. Only identical broker timestamps are paired: asynchronous
    latest ticks must not be passed off as a simultaneous bid/ask quote.
    No subscriptions, recovery, order requests, DB access or connection starts.
    """
    from ctrader_open_api.messages import OpenApiMessages_pb2 as M
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOAQuoteType,
        ProtoOASymbolDistanceType,
    )

    as_of = utc(as_of)
    if as_of.tzinfo is None:
        raise ValueError("Broker query requires timezone-aware as_of")
    client = provider.client
    if (
        client.environment != "demo"
        or not client.authenticated
        or not client.demo_verified
    ):
        raise ValueError(
            "An existing authenticated, verified DEMO connection is required"
        )
    metadata = provider.get_symbol_metadata("XAUUSD")
    tick = provider.price_tick(metadata)
    values = dict(source="ctrader_demo", digits=metadata.digits)
    unavailable = []
    for field, target in (
        ("slDistance", "min_stop_distance"),
        ("tpDistance", "min_target_distance"),
    ):
        if not metadata.HasField(field):
            unavailable.append(target + ": symbol metadata field absent")
        elif (
            metadata.distanceSetIn
            != ProtoOASymbolDistanceType.SYMBOL_DISTANCE_IN_POINTS
        ):
            unavailable.append(target + ": percentage/unknown units not normalized")
        else:
            values[target] = float(Decimal(getattr(metadata, field)) * tick)
    unavailable.append(
        "additional_round_trip_cost: no verified commission/slippage estimate in price units"
    )
    if spot_event is not None:
        if (
            not isinstance(spot_event, M.ProtoOASpotEvent)
            or not spot_event.IsInitialized()
            or spot_event.ctidTraderAccountId != client.account_id
            or spot_event.symbolId != metadata.symbolId
        ):
            raise ValueError(
                "Mismatched cTrader spot account/symbol or malformed event"
            )
        if all(spot_event.HasField(field) for field in ("bid", "ask", "timestamp")):
            values.update(
                bid=spot_event.bid / 100000,
                ask=spot_event.ask / 100000,
                observed_at=datetime.fromtimestamp(
                    spot_event.timestamp / 1000, timezone.utc
                ),
            )
        else:
            unavailable.append("quote: spot event lacks bid, ask or broker timestamp")
    else:
        end = int(as_of.timestamp() * 1000)
        ticks = []
        for quote_type in (ProtoOAQuoteType.BID, ProtoOAQuoteType.ASK):
            response = client.send(
                M.ProtoOAGetTickDataReq(
                    ctidTraderAccountId=client.account_id,
                    symbolId=metadata.symbolId,
                    type=quote_type,
                    fromTimestamp=max(0, end - 60000),
                    toTimestamp=end,
                )
            )
            if (
                not isinstance(response, M.ProtoOAGetTickDataRes)
                or not response.IsInitialized()
                or response.ctidTraderAccountId != client.account_id
            ):
                raise ValueError("Invalid cTrader tick response/account")
            ticks.append(response.tickData[0] if response.tickData else None)
        if (
            all(t is not None for t in ticks)
            and ticks[0].timestamp == ticks[1].timestamp
            and end - 60000 <= ticks[0].timestamp <= end
        ):
            values.update(
                bid=ticks[0].tick / 100000,
                ask=ticks[1].tick / 100000,
                observed_at=datetime.fromtimestamp(
                    ticks[0].timestamp / 1000, timezone.utc
                ),
            )
        else:
            unavailable.append(
                "quote: no matching-timestamp BID/ASK ticks in last 60 seconds"
            )
    return BrokerEvidenceSnapshot(**values, unavailable=unavailable)


def input_from_services(
    analysis_service,
    *,
    evaluated_at: datetime | None = None,
    broker_provider=None,
    spot_event=None
) -> tuple[StrategyInput, BrokerEvidenceSnapshot | None]:
    """Explicit worker-thread entry point; never constructs/starts a broker client.

    An unavailable broker can be omitted: market setup evaluation still runs,
    while validation reports execution inputs as missing. Errors from a supplied
    broker are surfaced rather than replaced with fabricated evidence.
    """
    analysis = analysis_service.analyze_multi_timeframe("XAUUSD")
    query_time = evaluated_at or datetime.now(timezone.utc)
    snapshot = (
        collect_broker_snapshot(
            broker_provider, as_of=query_time, spot_event=spot_event
        )
        if broker_provider is not None
        else None
    )
    # Live evaluation time is taken after fetching; source observation times are untouched.
    return (
        adapt_strategy_input(
            analysis,
            evaluated_at=evaluated_at or datetime.now(timezone.utc),
            broker_snapshot=snapshot,
            market_source="twelve_data",
        ),
        snapshot,
    )
