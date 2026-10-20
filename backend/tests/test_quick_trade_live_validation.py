from decimal import Decimal
from unittest.mock import Mock, patch

import pytest
from pydantic import ValidationError
from ctrader_open_api.messages import OpenApiMessages_pb2 as M
from ctrader_open_api.messages.OpenApiModelMessages_pb2 import ProtoOASymbol, ProtoOACommissionType as C, ProtoOAMinCommissionType as Min

from app.strategies.validation_costs import ValidationCostConfig, commission_estimate
from scripts.validate_quick_trade_live import run_validation, main, build_broker, build_analysis_service
from tests.test_quick_trade_input_adapter import snapshot
from tests.test_quick_trade_strategy import scenario


def metadata(**changes):
    fields = dict(symbolId=41, digits=2, pipPosition=1, lotSize=10000, slDistance=50, tpDistance=75,
                  commissionType=C.USD_PER_LOT, preciseTradingCommissionRate=300000000,
                  preciseMinCommission=0)
    fields.update(changes)
    return ProtoOASymbol(**fields)


@pytest.mark.parametrize('value', [-1, float('nan'), float('inf')])
def test_slippage_invalid(value):
    with pytest.raises(ValidationError): ValidationCostConfig(slippage_allowance=value)


def test_slippage_required_and_explicit_zero_allowed():
    with pytest.raises(ValidationError): ValidationCostConfig()
    assert ValidationCostConfig(slippage_allowance=0).slippage_allowance == 0


@pytest.mark.parametrize('kind,rate,expected', [(C.USD_PER_LOT,300000000,.06),
    (C.QUOTE_CCY_PER_LOT,300000000,.06), (C.USD_PER_MILLION_USD,5000000000,.42),
    (C.PERCENTAGE_OF_VALUE,100000,84)])
def test_precise_commission_scales_and_two_sides(kind, rate, expected):
    result = commission_estimate(metadata(commissionType=kind,preciseTradingCommissionRate=rate),reference_price=4200, volume_lots=.01)
    assert result['round_trip_price_cost'] == pytest.approx(expected)
    assert result['raw_metadata']['preciseTradingCommissionRate'] == rate


def test_minimum_fee_is_applied_per_side():
    result = commission_estimate(metadata(preciseMinCommission=10000000,minCommissionType=Min.QUOTE_CURRENCY),reference_price=4200,volume_lots=.01)
    assert result['round_trip_price_cost'] == .2


@pytest.mark.parametrize('field', ['preciseTradingCommissionRate','preciseMinCommission','commissionType','lotSize'])
def test_missing_fee_metadata_never_zero(field):
    symbol = metadata()
    symbol.ClearField(field)
    result = commission_estimate(symbol,reference_price=4200,volume_lots=.01)
    assert result['round_trip_price_cost'] is None
    assert result['unavailable_reason']


def test_explicit_zero_commission_is_supported():
    assert commission_estimate(metadata(preciseTradingCommissionRate=0),reference_price=4200,volume_lots=.01)['round_trip_price_cost'] == 0


def test_foreign_minimum_fee_not_guessed():
    result = commission_estimate(metadata(preciseMinCommission=10000000,minCommissionType=Min.CURRENCY,minCommissionAsset='EUR'),reference_price=4200,volume_lots=.01)
    assert result['round_trip_price_cost'] is None


def fixtures():
    analysis = Mock()
    analysis.analyze_multi_timeframe.return_value = scenario().analysis
    provider = Mock()
    provider.client.environment = 'demo'
    provider.client.authenticated = True
    provider.client.demo_verified = True
    provider.client.account_id = 123
    provider.client.wait_until_authenticated.return_value = True
    provider.get_symbol_metadata.return_value = metadata()
    def send(request):
        if isinstance(request,M.ProtoOASymbolsListReq):
            result = M.ProtoOASymbolsListRes(ctidTraderAccountId=123)
            result.symbol.add(symbolId=41,symbolName='XAUUSD',quoteAssetId=1)
            return result
        if isinstance(request,M.ProtoOAAssetListReq):
            result = M.ProtoOAAssetListRes(ctidTraderAccountId=123)
            result.asset.add(assetId=1,name='USD')
            return result
        raise AssertionError('Unexpected broker request')
    provider.client.send.side_effect = send
    return analysis,provider


def run(analysis,provider):
    with patch('scripts.validate_quick_trade_live.collect_broker_snapshot',return_value=snapshot(additional_round_trip_cost=None)):
        return run_validation(ValidationCostConfig(slippage_allowance=.1),analysis_service=analysis,provider=provider)


def test_complete_evidence_separates_allowance_from_realized_slippage():
    analysis,provider = fixtures()
    report = run(analysis,provider)
    assert report['costs']['commission']['round_trip_price_cost'] == .06
    assert report['costs']['slippage_allowance'] == .1
    assert report['costs']['realized_slippage'] is None
    assert report['broker_snapshot']['additional_round_trip_cost'] == .16
    assert report['disconnected']
    provider.client.start.assert_called_once()
    provider.client.stop.assert_called_once()
    assert report['validation']['missing_inputs'] == []
    assert report['validation']['evaluation']['market_timestamp'].startswith('2026-10-19T10:00:00')


def test_separate_monotonic_latencies():
    analysis,provider = fixtures()
    with patch('scripts.validate_quick_trade_live.perf_counter_ns',side_effect=[0,1000000,3000000,4000000,9000000,15000000]):
        report = run(analysis,provider)
    assert report['latency_ms']['market_data'] == 2
    assert report['latency_ms']['broker_evidence'] == 5
    assert report['latency_ms']['total'] == 15
    assert report['latency_ms']['strategy_evaluation'] >= 0


def test_authentication_failure_stops_client_and_reports_missing_evidence():
    analysis,provider = fixtures()
    provider.client.wait_until_authenticated.return_value = False
    report = run(analysis,provider)
    assert report['outcome'] == 'incomplete_input'
    assert 'authenticated/verified' in report['errors'][0]['message']
    assert report['latency_ms']['broker_evidence'] >= 0
    provider.client.stop.assert_called_once()
    provider.get_symbol_metadata.assert_not_called()


def test_live_configuration_rejected_before_start():
    analysis,provider = fixtures()
    provider.client.environment = 'live'
    report = run(analysis,provider)
    provider.client.start.assert_not_called()
    assert 'DEMO' in report['errors'][0]['message']


def test_market_service_failure_does_not_disclose_key():
    analysis,provider = fixtures()
    analysis.analyze_multi_timeframe.side_effect = RuntimeError('https://example.com/?apikey=SECRET')
    report = run(analysis,provider)
    assert report['outcome'] == 'data_unavailable'
    assert 'SECRET' not in str(report)
    assert report['latency_ms']['strategy_evaluation'] is None
    provider.client.stop.assert_called_once()


def test_missing_commission_stays_incomplete_even_with_slippage_allowance():
    analysis,provider = fixtures()
    symbol = metadata()
    symbol.ClearField('preciseTradingCommissionRate')
    provider.get_symbol_metadata.return_value = symbol
    with patch('scripts.validate_quick_trade_live.collect_broker_snapshot', return_value=snapshot(additional_round_trip_cost=None)):
        report = run_validation(ValidationCostConfig(slippage_allowance=.1),analysis_service=analysis,provider=provider)
    assert report['outcome'] == 'incomplete_input'
    assert report['broker_snapshot']['additional_round_trip_cost'] is None


def test_missing_environment_data_explicit():
    with patch.dict('os.environ',{},clear=True),pytest.raises(ValueError,match='TWELVE_DATA_API_KEY'):
        build_analysis_service()


def test_factory_forbids_order_and_close_requests():
    with patch('app.integrations.ctrader.client.CTraderClient') as cls, patch('app.providers.ctrader_broker_provider.CTraderBrokerProvider') as factory:
        build_broker()
        client = cls.return_value
        guarded = client.send
        with pytest.raises(ValueError,match='forbids'): guarded(M.ProtoOANewOrderReq())
        with pytest.raises(ValueError,match='forbids'): guarded(M.ProtoOAClosePositionReq())
        assert not client.order_guard() and not client.close_guard()
        cls.assert_called_once()


def test_quote_asset_mismatch_rejects_fee_normalization():
    analysis,provider = fixtures()
    provider.client.send.return_value = None
    provider.client.send.side_effect = lambda request: M.ProtoOAAssetListRes(ctidTraderAccountId=999)
    report = run(analysis,provider)
    assert report['errors'][0]['stage'] == 'broker_evidence'
    assert report['outcome'] == 'incomplete_input'


def test_command_requires_explicit_slippage_allowance():
    with pytest.raises(SystemExit): main([])


def test_complete_report_can_be_replayed_without_refreshing_timestamps():
    from scripts.validate_quick_trade import evaluate_raw
    analysis,provider = fixtures()
    report = run(analysis,provider)
    replay = evaluate_raw(report,'live-capture',replay=True)
    assert replay['outcome'] == report['outcome']
    assert replay['evaluation']['market_timestamp'] == report['validation']['evaluation']['market_timestamp']
    assert replay['execution_observations']['observed_at'] == report['broker_snapshot']['observed_at']


def test_shutdown_failure_reported_without_claiming_disconnection():
    analysis,provider = fixtures()
    provider.client.stop.side_effect = RuntimeError('stop failed')
    report = run(analysis,provider)
    assert not report['disconnected']
    assert report['errors'][-1]['stage'] == 'disconnect'
    assert report['latency_ms']['total'] >= 0


def test_invalid_broker_quote_reports_input_error_after_clean_shutdown():
    analysis,provider = fixtures()
    with patch('scripts.validate_quick_trade_live.collect_broker_snapshot',return_value=snapshot(ask=4199)):
        report = run_validation(ValidationCostConfig(slippage_allowance=.1),analysis_service=analysis,provider=provider)
    assert report['errors'][-1]['stage'] == 'validation_input'
    assert report['latency_ms']['strategy_evaluation'] is None
    assert report['disconnected']
