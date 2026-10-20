"""Explicit pre-trade estimates; never represents realized execution costs."""

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ValidationCostConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    # Required and explicitly chosen by the operator; round-trip XAUUSD price units.
    slippage_allowance: float = Field(ge=0)


def commission_estimate(
    metadata, *, reference_price, volume_lots, quote_currency="USD"
):
    """Estimate two charged sides; preserve raw metadata and fail closed on unknowns.

    Uses the precise Open API rates (1e8 for non-percentage, 1e5 for percent).
    Legacy rates alone, foreign-currency minima and missing minimum fees remain
    unknown. Output is in XAUUSD price units for the existing configured volume.
    """
    from ctrader_open_api.messages.OpenApiModelMessages_pb2 import (
        ProtoOACommissionType as C,
        ProtoOAMinCommissionType as Min,
    )

    fields = (
        "commission",
        "commissionType",
        "preciseTradingCommissionRate",
        "minCommission",
        "preciseMinCommission",
        "minCommissionType",
        "minCommissionAsset",
        "lotSize",
    )
    raw = {
        name: getattr(metadata, name) if metadata.HasField(name) else None
        for name in fields
    }
    result = {
        "raw_metadata": raw,
        "round_trip_price_cost": None,
        "basis": "two charged sides at current reference price; pre-trade estimate",
        "unavailable_reason": None,
    }
    required = (
        "preciseTradingCommissionRate",
        "commissionType",
        "preciseMinCommission",
        "lotSize",
    )
    if any(raw[name] is None for name in required):
        result["unavailable_reason"] = (
            "Missing precise commission rate/type, precise minimum commission or lot size"
        )
        return result
    rate = Decimal(raw["preciseTradingCommissionRate"])
    minimum = Decimal(raw["preciseMinCommission"]) / Decimal(10**8)
    lots, price = Decimal(str(volume_lots)), Decimal(str(reference_price))
    lot_units = Decimal(raw["lotSize"]) / 100
    if (
        rate < 0
        or minimum < 0
        or lots <= 0
        or lot_units <= 0
        or price <= 0
        or quote_currency != "USD"
    ):
        result["unavailable_reason"] = (
            "Unsupported quote currency or invalid commission inputs"
        )
        return result
    if minimum and not (
        raw["minCommissionType"] == Min.QUOTE_CURRENCY
        or raw["minCommissionType"] == Min.CURRENCY
        and raw["minCommissionAsset"] == "USD"
    ):
        result["unavailable_reason"] = (
            "Minimum commission currency is absent or requires conversion"
        )
        return result
    kind = raw["commissionType"]
    units = lots * lot_units
    if kind == C.USD_PER_MILLION_USD:
        charge = rate / Decimal(10**8) * (price * units) / 1000000
    elif kind in (C.USD_PER_LOT, C.QUOTE_CCY_PER_LOT):
        charge = rate / Decimal(10**8) * lots
    elif kind == C.PERCENTAGE_OF_VALUE:
        charge = rate / Decimal(10**5) / 100 * price * units
    else:
        result["unavailable_reason"] = "Unsupported commission type"
        return result
    result["commission_type"] = C.Name(kind)
    result["round_trip_price_cost"] = float(2 * max(charge, minimum) / units)
    return result


def verified_commission_estimate(provider, *, reference_price, volume_lots):
    """Read-only account-scoped metadata and quote-asset verification."""
    from ctrader_open_api.messages import OpenApiMessages_pb2 as M

    client = provider.client
    if (
        client.environment != "demo"
        or not client.authenticated
        or not client.demo_verified
    ):
        raise ValueError("Verified authenticated DEMO broker required")
    metadata = provider.get_symbol_metadata("XAUUSD")
    symbols = client.send(
        M.ProtoOASymbolsListReq(ctidTraderAccountId=client.account_id)
    )
    assets = client.send(M.ProtoOAAssetListReq(ctidTraderAccountId=client.account_id))
    if (
        not isinstance(symbols, M.ProtoOASymbolsListRes)
        or symbols.ctidTraderAccountId != client.account_id
        or not isinstance(assets, M.ProtoOAAssetListRes)
        or assets.ctidTraderAccountId != client.account_id
    ):
        raise ValueError("Cannot verify XAUUSD quote asset")
    matching = [
        s
        for s in symbols.symbol
        if s.symbolId == metadata.symbolId and s.symbolName == "XAUUSD"
    ]
    if len(matching) != 1 or not matching[0].HasField("quoteAssetId"):
        raise ValueError("Missing or ambiguous XAUUSD quote asset")
    if [a.name for a in assets.asset if a.assetId == matching[0].quoteAssetId] != [
        "USD"
    ]:
        raise ValueError("XAUUSD quote asset must be verified USD")
    return commission_estimate(
        metadata,
        reference_price=reference_price,
        volume_lots=volume_lots,
        quote_currency="USD",
    )
