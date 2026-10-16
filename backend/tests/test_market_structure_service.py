from dataclasses import dataclass

import pytest

from app.services.market_structure_service import MarketStructureService


@dataclass
class Candle:
    high: float
    low: float
    close: float


@pytest.fixture
def service():
    return MarketStructureService()


def highs(prices):
    return [Candle(p, p - 2, p - 1) for p in prices]


def lows(prices):
    return [Candle(p + 2, p, p + 1) for p in prices]


@pytest.mark.parametrize("side", ["high", "low"])
@pytest.mark.parametrize("values,indices", [
    pytest.param([10, 11, 15, 12, 11], [2], id="confirmed"),
    pytest.param([10, 11, 12, 13, 14], [], id="monotonic"),
    pytest.param([10, 10, 10, 10, 10], [], id="flat"),
    pytest.param([15, 11, 15, 12, 11], [], id="equal-left-outer"),
    pytest.param([10, 15, 15, 12, 11], [], id="equal-left-inner"),
    pytest.param([10, 11, 15, 15, 11], [], id="equal-right-inner"),
    pytest.param([10, 11, 15, 12, 15], [], id="equal-right-outer"),
    pytest.param([20, 10, 9, 8, 7], [], id="first-edge"),
    pytest.param([10, 20, 9, 8, 7], [], id="left-unconfirmed"),
    pytest.param([7, 8, 9, 20, 10], [], id="right-unconfirmed"),
    pytest.param([7, 8, 9, 10, 20], [], id="last-edge"),
    pytest.param([], [], id="empty"),
    pytest.param([10], [], id="single"),
    pytest.param([10, 15, 12, 11], [], id="too-short"),
    pytest.param([10, 11, 15, 11, 10, 16, 11, 10], [2, 5], id="ordered-swings"),
])
def test_confirmed_swings(service, side, values, indices):
    # Reflection tests the same strict boundaries for lows as for highs.
    candles = highs(values) if side == "high" else lows([-v for v in values])
    find = service.find_swing_highs if side == "high" else service.find_swing_lows
    assert find(candles) == [candles[i] for i in indices]


@pytest.mark.parametrize("side", ["high", "low"])
@pytest.mark.parametrize("window,indices", [(1, [1, 3, 5]), (2, [3]), (3, [3]), (4, [])])
def test_configurable_window(service, side, window, indices):
    values = [1, 5, 2, 8, 2, 5, 1]
    candles = highs(values) if side == "high" else lows([-v for v in values])
    find = service.find_swing_highs if side == "high" else service.find_swing_lows
    assert find(candles, window=window) == [candles[i] for i in indices]


@pytest.mark.parametrize("side", ["high", "low"])
@pytest.mark.parametrize("prices,pattern", [
    ([], "uncertain"), ([10], "uncertain"), ([10, 12], "higher"),
    ([12, 10], "lower"), ([10, 10], "equal"),
    ([100, 10, 12], "higher"), ([1, 12, 10], "lower"),
])
def test_patterns_use_two_latest_swings(service, side, prices, pattern):
    classify = service.classify_high_pattern if side == "high" else service.classify_low_pattern
    swings = highs(prices) if side == "high" else lows(prices)
    expected = "uncertain" if pattern == "uncertain" else f"{pattern}_{side}"
    assert classify(swings) == expected


@pytest.mark.parametrize("high_prices,low_prices,expected", [
    ([20, 22], [10, 12], "bullish"), ([22, 20], [12, 10], "bearish"),
    ([20, 22], [12, 10], "mixed"), ([22, 20], [10, 12], "mixed"),
    ([20, 20], [10, 12], "mixed"), ([20, 20], [12, 10], "mixed"),
    ([20, 22], [10, 10], "mixed"), ([22, 20], [10, 10], "mixed"),
    ([20, 20], [10, 10], "mixed"),
    ([], [10, 12], "uncertain"), ([20], [10, 12], "uncertain"),
    ([20, 22], [], "uncertain"), ([20, 22], [10], "uncertain"),
    ([], [], "uncertain"),
])
def test_overall_structure(service, high_prices, low_prices, expected):
    assert service.classify_structure(highs(high_prices), lows(low_prices)) == expected


@pytest.mark.parametrize("latest,high_prices,low_prices,expected", [
    pytest.param(Candle(115, 105, 111), [120, 110], [80, 90], "break_above", id="above-latest-high"),
    pytest.param(Candle(95, 85, 89), [120, 110], [80, 90], "break_below", id="below-latest-low"),
    pytest.param(Candle(105, 95, 100), [110], [90], "none", id="inside"),
    pytest.param(Candle(115, 95, 109), [110], [90], "none", id="upper-wick-only"),
    pytest.param(Candle(105, 85, 91), [110], [90], "none", id="lower-wick-only"),
    pytest.param(Candle(115, 95, 110), [110], [90], "none", id="equal-high"),
    pytest.param(Candle(105, 85, 90), [110], [90], "none", id="equal-low"),
    pytest.param(Candle(115, 105, 111), [110], [], "break_above", id="no-support"),
    pytest.param(Candle(95, 85, 89), [], [90], "break_below", id="no-resistance"),
    pytest.param(Candle(105, 95, 100), [], [], "none", id="no-levels"),
])
def test_structure_break_uses_latest_close(service, latest, high_prices, low_prices, expected):
    candles = [Candle(200, 0, 150), latest]
    assert service.detect_structure_break(candles, highs(high_prices), lows(low_prices)) == expected


def test_structure_break_empty_candles(service):
    assert service.detect_structure_break([], highs([110]), lows([90])) == "none"


@pytest.mark.parametrize("high_prices,low_prices,resistance,support", [
    ([120, 110], [80, 90], 110, 90), ([], [80, 90], None, 90),
    ([120, 110], [], 110, None), ([], [], None, None),
])
def test_key_levels(service, high_prices, low_prices, resistance, support):
    assert service.get_key_levels(highs(high_prices), lows(low_prices)) == {
        "resistance": resistance, "support": support,
    }


@pytest.mark.parametrize("close,high_prices,low_prices,to_high,to_low", [
    (100, [120, 110], [80, 90], 10, 10),
    (112, [110], [90], -2, 22), (88, [110], [90], 22, -2),
    (110, [110], [90], 0, 20), (90, [110], [90], 20, 0),
    (100, [100.123456], [99.876544], 0.12346, 0.12346),
    (100, [99.876544], [], -0.12346, None),
    (100, [], [100.123456], None, -0.12346),
    (100, [], [90], None, 10), (100, [110], [], 10, None),
    (100, [], [], None, None),
])
def test_distances(service, close, high_prices, low_prices, to_high, to_low):
    candles = [Candle(200, 0, 150), Candle(close + 1, close - 1, close)]
    assert service.distance_to_levels(candles, highs(high_prices), lows(low_prices)) == {
        "distance_to_swing_high": to_high, "distance_to_swing_low": to_low,
    }


def test_distances_empty_candles(service):
    assert service.distance_to_levels([], highs([110]), lows([90])) == {
        "distance_to_swing_high": None, "distance_to_swing_low": None,
    }


@pytest.mark.parametrize("bearish", [False, True], ids=["bullish", "bearish"])
def test_analyze_complete_structure(service, bearish):
    # Window 2 confirms highs at 2/8 and lows at 5/11. The final candle
    # breaks resistance but cannot itself be confirmed as a swing.
    prices = [10, 11, 15, 12, 10, 8, 11, 13, 18, 14, 12, 10, 13, 14, 20]
    candles = [Candle(p + 1, p - 1, p) for p in prices]
    if bearish:
        candles = [Candle(40 - c.low, 40 - c.high, 40 - c.close) for c in candles]
    result = service.analyze(candles)
    expected_highs = [candles[5], candles[11]] if bearish else [candles[2], candles[8]]
    expected_lows = [candles[2], candles[8]] if bearish else [candles[5], candles[11]]
    assert result == {
        "structure": "bearish" if bearish else "bullish",
        "high_pattern": "lower_high" if bearish else "higher_high",
        "low_pattern": "lower_low" if bearish else "higher_low",
        "structure_break": "break_below" if bearish else "break_above",
        "latest_swing_high": expected_highs[-1], "latest_swing_low": expected_lows[-1],
        "support": 21 if bearish else 9, "resistance": 31 if bearish else 19,
        "distance_to_swing_high": 11 if bearish else -1,
        "distance_to_swing_low": -1 if bearish else 11,
        "swing_highs": expected_highs, "swing_lows": expected_lows,
    }
    assert result["latest_swing_high"] is result["swing_highs"][-1]
    assert result["latest_swing_low"] is result["swing_lows"][-1]
    assert result["resistance"] == result["latest_swing_high"].high
    assert result["support"] == result["latest_swing_low"].low


@pytest.mark.parametrize("size", [0, 1, 2, 3, 4])
def test_analyze_insufficient_candles(service, size):
    assert service.analyze(highs(range(10, 10 + size))) == {
        "structure": "uncertain", "high_pattern": "uncertain", "low_pattern": "uncertain",
        "structure_break": "none", "latest_swing_high": None, "latest_swing_low": None,
        "support": None, "resistance": None,
        "distance_to_swing_high": None, "distance_to_swing_low": None,
        "swing_highs": [], "swing_lows": [],
    }


def test_analyze_single_confirmed_swing(service):
    candles = highs([10, 11, 15, 12, 11])
    result = service.analyze(candles)
    assert result["structure"] == result["high_pattern"] == result["low_pattern"] == "uncertain"
    assert result["swing_highs"] == [candles[2]]
    assert result["swing_lows"] == []
    assert result["resistance"] == 15
    assert result["support"] is None
    assert result["distance_to_swing_high"] == 5
    assert result["distance_to_swing_low"] is None


def test_analyze_passes_custom_window(service):
    candles = [Candle(p + 1, p - 1, p) for p in [1, 5, 2, 8, 2, 5, 1]]
    result = service.analyze(candles, window=1)
    assert result["swing_highs"] == [candles[1], candles[3], candles[5]]
    assert result["swing_lows"] == [candles[2], candles[4]]
    assert result["high_pattern"] == "lower_high"
    assert result["low_pattern"] == "equal_low"
    assert result["structure"] == "mixed"
