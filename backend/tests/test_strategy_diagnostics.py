from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.exc import OperationalError

from app.database import Base
from app.db_models import StrategyEvaluationRecord
from app.services.strategy_diagnostics import summarize_evaluations, load_evaluations, distribution
from app.strategies.price_consistency import compare_prices
from scripts.summarize_quick_trade import main

NOW=datetime(2026,10,20,9,17,14,tzinfo=timezone.utc)


def actual_rows():
    return json.loads((Path(__file__).parent/'fixtures/quick_trade_persisted_sample.json').read_text())['rows']


def test_actual_persisted_snapshot_no_invented_opportunities():
    rows=actual_rows()
    before=deepcopy(rows)
    report=summarize_evaluations(rows)
    assert report['total_evaluations']==2
    assert report['counts']==dict(eligible=0,rejected=1,incomplete=0,blocked=1,error=0,not_evaluated=0)
    assert report['rejections_by_code']=={'volatility_unsuitable':1,'reaction_evidence_missing':1}
    assert report['setup_types']=={'not_evaluated':1,'not_identified':1}
    assert report['atr']['values']['count']==1 and report['atr']['values']['median']==3.8779
    assert report['atr']['above_max']==1 and report['atr']['ratio_above_max']==0
    assert report['latency_ms']['detection_ms']['median']==7086.603334
    assert report['latency_ms']['strategy_evaluation_ms']['median']==.564334
    assert report['latency_ms']['ai_reasoning_ms']['count']==0
    assert report['funnel']['confirmed_full_fills']==0
    assert report['funnel']['eligible_reached_ollama_confirmed']==0
    prices=report['price_discrepancy']
    assert prices['comparable_pairs']==1 and prices['pairs_with_actual_tp']==0
    assert prices['absolute_mid_gap']['median']==1.01295
    assert prices['pairs'][0]['time_skew_seconds']==.595416
    assert prices['pairs'][0]['actual_tp_fraction'] is None
    assert prices['pairs'][0]['market_event_alignment_confirmed'] is False
    assert prices['exceeds_fraction_of_current_min_target']==1
    assert prices['exceeds_fraction_of_current_max_target']==1
    assert 'Insufficient' in report['sample_assessment']['note']
    assert rows==before


def test_missing_observations_remain_unknown():
    report=summarize_evaluations([{'status':'skipped','data':{'block_reason':'trading_paused'}}])
    assert report['atr']['values']['count']==0
    assert report['atr']['values']['mean'] is None
    assert report['price_discrepancy']['comparable_pairs']==0
    assert report['latency_ms']['detection_ms']['min'] is None
    assert summarize_evaluations([])['total_evaluations']==0


@pytest.mark.parametrize('reference,bid,ask',[(None,1,2),(float('nan'),1,2),(1,2,1),(1,0,2),(float('inf'),1,2)])
def test_invalid_quotes_not_counted_as_zero_difference(reference,bid,ask):
    assert compare_prices(reference,bid,ask,NOW,NOW)['status']=='missing_or_invalid_prices'


def test_exact_difference_offsets_and_actual_tp_denominator():
    pair=compare_prices(4125.75295,4124.69,4124.79,NOW.astimezone(timezone(timedelta(hours=2))),NOW,target_distance=2)
    assert pair['status']=='comparable'
    assert pair['absolute_mid_gap']==1.01295
    assert pair['reference_minus_bid']==1.06295
    assert pair['reference_minus_ask']==.96295
    assert pair['actual_tp_fraction']==.506475


@pytest.mark.parametrize('stamp,status',[(NOW-timedelta(seconds=6),'timestamp_skew_exceeded'),
    (NOW.replace(tzinfo=None),'missing_or_naive_timestamps'),(None,'missing_or_naive_timestamps')])
def test_incomparable_timestamps_excluded(stamp,status):
    assert compare_prices(10,9,9.1,stamp,NOW)['status']==status


def eligible_row():
    row=deepcopy(actual_rows()[1])
    row['data']['evaluation']['outcome']='eligible'
    row['data']['evaluation']['evaluation'].update(status='eligible',setup_type='range_support_bounce',proposal={'target_distance':2},rejection_reasons=[])
    row['data']['evaluation']['input_failure_reasons']=[]
    row['data']['evaluation']['strategy_rejection_reasons']=[]
    row['data']['timing']['agent_stage_started_at']=NOW.isoformat()
    return row


def test_funnel_does_not_equate_agent_stage_or_prepared_request_with_orders():
    row=eligible_row()
    row.update(request_id='request',execution_status='prepared',agent_decision_id='decision')
    report=summarize_evaluations([row])
    assert report['funnel']['eligible_ollama_unknown']==1
    assert report['funnel']['eligible_reached_ollama_confirmed']==0
    assert report['funnel']['execution_intents']==1
    assert report['funnel']['confirmed_submissions']==0
    row['agent_macro_context']={'allow_new_trade':False}
    assert summarize_evaluations([row])['funnel']['eligible_ollama_unknown']==0


def test_funnel_counts_proven_reasoning_and_linked_confirmed_fills():
    row=eligible_row()
    row['data']['timing']['ai_reasoning']={'started_at':NOW.isoformat(),'duration_ms':100}
    row.update(request_id='request',execution_status='filled',broker_order_id='broker',filled_volume_lots=.01,
               execution_request_data={'execution_tracking':{'submission_to_fill_confirmation_ms':150}})
    report=summarize_evaluations([row])
    assert report['funnel']['eligible_reached_ollama_confirmed']==1
    assert report['funnel']['confirmed_submissions']==1
    assert report['funnel']['confirmed_full_fills']==1
    assert report['latency_ms']['submission_to_fill_confirmation_ms']['mean']==150
    assert report['price_discrepancy']['pairs_with_actual_tp']==1
    assert report['price_discrepancy']['exceeds_fraction_of_actual_tp']==1


def test_partial_fill_does_not_become_full_fill():
    row=eligible_row()
    row.update(request_id='request',execution_status='pending',filled_volume_lots=.005)
    report=summarize_evaluations([row])
    assert report['funnel']['confirmed_full_fills']==0
    assert report['funnel']['confirmed_any_fills']==1


def test_persisted_limits_used_and_reason_codes_deduplicated():
    row=actual_rows()[1]
    row['data']['strategy_config']={'min_atr':.75,'max_atr':4,'max_atr_ratio':1.75}
    row['data']['evaluation']['evaluation']['rejection_reasons'].append({'code':'volatility_unsuitable'})
    report=summarize_evaluations([row])
    assert report['atr']['above_max']==0
    assert report['atr']['rows_with_persisted_limits']==1
    assert report['rejections_by_code']['volatility_unsuitable']==1


def test_revalidation_not_counted_as_another_opportunity():
    row=actual_rows()[1]
    row['data']['pre_submission']={'evaluation':deepcopy(row['data']['evaluation'])}
    report=summarize_evaluations([row])
    assert report['total_evaluations']==1
    assert report['price_discrepancy']['comparable_pairs']==1


def test_quantiles_and_invalid_latencies():
    assert distribution([1,2,3,4])['median']==2.5
    assert distribution([float('nan'),None])['count']==0
    row=actual_rows()[1]
    row['data']['timing']={'detection_ms':-1,'strategy_evaluation_ms':float('inf')}
    assert summarize_evaluations([row])['latency_ms']['detection_ms']['count']==0


def test_sqlite_loader_is_read_only_and_filters_dates(tmp_path):
    engine=create_engine(f'sqlite:///{tmp_path / "report.db"}')
    Base.metadata.create_all(engine)
    table=StrategyEvaluationRecord.__table__
    with engine.begin() as conn:
        conn.execute(table.insert(),[dict(id='one',strategy_id='quick_trade_v1',symbol='XAUUSD',created_at=NOW,
            updated_at=NOW,status='skipped',data={}),dict(id='two',strategy_id='quick_trade_v1',symbol='XAUUSD',
            created_at=NOW+timedelta(hours=1),updated_at=NOW,status='skipped',data={})])
    rows=load_evaluations(engine,since=NOW,until=NOW+timedelta(minutes=1))
    assert [row['id'] for row in rows]==['one']
    with pytest.raises(OperationalError),engine.begin() as conn:
        conn.execute(update(table).values(status='changed'))
    engine.dispose()


def test_offline_command_needs_no_network_or_database(capsys):
    path=Path(__file__).parent/'fixtures/quick_trade_persisted_sample.json'
    with patch('socket.socket',side_effect=AssertionError('network forbidden')):
        report=main(['--from-json',str(path)])
    assert report['total_evaluations']==2
    assert json.loads(capsys.readouterr().out)['source']['type']=='offline_snapshot'


@pytest.mark.parametrize('args',[['--since','2026-10-20'],['--max-time-skew-seconds','nan'],
    ['--meaningful-tp-fraction','0'],['--since','2026-10-21T00:00:00Z','--until','2026-10-20T00:00:00Z']])
def test_invalid_report_parameters_rejected(args):
    with pytest.raises(SystemExit):main(args)


def test_unavailable_database_is_explicit_and_no_credentials_printed(capsys):
    with patch('dotenv.load_dotenv'),patch.dict('os.environ',{'DATABASE_URL':'sqlite:///:memory:'}),patch(
        'scripts.summarize_quick_trade.load_evaluations',side_effect=RuntimeError('secret password')):
        with pytest.raises(SystemExit) as error: main([])
    assert 'Read-only diagnostics failed' in str(error.value)
    assert 'secret password' not in str(error.value)


def test_actual_timestamps_are_recent_receipts_not_known_exchange_times():
    pair=summarize_evaluations(actual_rows())['price_discrepancy']['pairs'][0]
    assert pair['reference_receipt_age_at_evaluation_seconds']==6.701329
    assert pair['broker_event_age_at_evaluation_seconds']==6.105913
    assert not pair['market_event_alignment_confirmed']


def test_no_tp_sample_cannot_report_zero_exceedance_rate():
    prices=summarize_evaluations(actual_rows())['price_discrepancy']
    assert prices['pairs_with_actual_tp']==0
    assert prices['actual_tp_exceedance_rate'] is None


def test_wrong_offline_format_not_misreported_as_zero_evaluations(tmp_path):
    path=tmp_path/'wrong.json'
    path.write_text('{"validation": {}}')
    with pytest.raises(SystemExit):main(['--from-json',str(path)])


def test_execution_gate_block_still_counts_as_a_market_evaluation():
    row=eligible_row()
    row['data']['evaluation']['outcome']='blocked'
    row['data']['timing'].pop('agent_stage_started_at')
    report=summarize_evaluations([row])
    assert report['counts']['blocked']==1
    assert report['sample_assessment']['market_evaluations']==1
    assert report['funnel']['raw_strategy_eligible']==1
    assert report['funnel']['eligible_after_input_execution_gates']==0
