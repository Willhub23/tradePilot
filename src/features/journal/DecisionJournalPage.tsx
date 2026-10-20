import { useEffect, useState } from 'react';
import { Card } from '../../components/ui/Card';
import { journalGet, journalQuery } from '../../services/journalApi';
import type { JournalDetail, JournalFilters, JournalHistory, JournalItem, JournalSummary, Signal, Values } from '../../services/journalApi';

const emptyFilters: JournalFilters = { since: '', until: '', outcome: '', setup: '', direction: '' };
const setups = ['range_support_bounce', 'range_resistance_rejection', 'trend_pullback_continuation', 'breakout'];
const outcomes = ['eligible', 'rejected', 'incomplete', 'blocked', 'ai_hold', 'ai_rejected', 'submitted', 'filled', 'broker_rejected', 'broker_cancelled', 'execution_uncertain', 'execution_pending'];
export const label = (value: string) => value.replaceAll('_', ' ');
function valueText(value: unknown): string {
  if (value === null || value === undefined) return 'Not recorded';
  if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: 5 });
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
function timestamp(value: string | null) { return value ? new Date(value).toLocaleString() : 'Not recorded'; }
function Fields({ values }: { values: Values }) {
  return <dl className="journal-fields">{Object.entries(values).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{valueText(value)}</dd></div>)}</dl>;
}
function Signals({ title, signals }: { title: string; signals: Signal[] }) {
  return <section><h3>{title}</h3>{signals.length ? signals.map((signal, i) => <div className="journal-signal" key={`${signal.name}-${i}`}><strong>{label(signal.name)}</strong><Fields values={signal.values} /></div>) : <p className="journal-muted">None recorded.</p>}</section>;
}
function Detail({ id }: { id: string }) {
  const [detail, setDetail] = useState<JournalDetail | null>(null);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController(); setDetail(null); setError('');
    journalGet<JournalDetail>(`evaluations/${encodeURIComponent(id)}`, controller.signal).then(setDetail).catch(e => { if (!controller.signal.aborted) setError(String(e.message)); });
    return () => controller.abort();
  }, [id, retry]);
  if (error) return <p role="alert">{error} <button onClick={() => setRetry(r => r + 1)}>Retry details</button></p>;
  if (!detail) return <p role="status">Loading persisted evaluation…</p>;
  const market = detail.market_conditions || {};
  const broker = detail.broker_snapshot || {};
  const discrepancy = detail.price_consistency || {};
  const proposal = detail.proposal;
  const tracking = ((detail.broker_execution?.request_data as Values | undefined)?.execution_tracking || {}) as Values;
  const reasoning = (detail.timing.ai_reasoning || {}) as Values;
  return <div className="journal-detail">
    <p>{detail.decision_reason || 'No strategy thesis was recorded for this cycle.'}</p>
    {detail.mode === 'observation' && <p className="journal-notice">Observation only — proposal eligibility did not authorize AI reasoning or an order.</p>}
    <section><h3>Trade proposal</h3>{proposal ? <Fields values={proposal} /> : <p className="journal-muted">No proposal recorded. Missing levels are not inferred.</p>}</section>
    <section><h3>Specific rejection and block reasons</h3>{detail.rejection_reasons.map((reason, i) => <p className="journal-reason" key={`${reason.code}-${i}`}><strong>{label(reason.code)}</strong> — {reason.message}{reason.measured != null && <> · measured {valueText(reason.measured)}</>}{reason.limit != null && <> · limit {valueText(reason.limit)}</>}</p>)}
      {detail.block_reason && <p>Pipeline block: {label(detail.block_reason)}</p>}
      {!detail.rejection_reasons.length && !detail.block_reason && <p className="journal-muted">No rejection recorded.</p>}
      {!!detail.missing_inputs.length && <p>Missing input: {detail.missing_inputs.join(', ')}</p>}{detail.input_error && <p>Input error: {detail.input_error}</p>}
      {detail.invalidation_condition && <p>Setup invalidation: {detail.invalidation_condition}</p>}
    </section>
    <div className="journal-columns"><Signals title="Supporting evidence" signals={detail.supporting_signals} /><Signals title="Conflicting evidence" signals={detail.conflicting_signals} /></div>
    <section><h3>Broader market context</h3><p className="journal-muted">Structure is context, not a direction veto. A resistance rejection can support a short scalp within a bullish structure.</p>
      <Fields values={Object.fromEntries(['structure','market_regime','volatility_regime','trend_direction','atr','atr_ratio','rsi','adx','support','resistance'].map(key => [key, market[key]]))} />
    </section>
    <section><h3>Execution evidence and price sources</h3><Fields values={{ reference_price: detail.reference_price, market_timestamp: detail.market_timestamp, broker_bid: broker.bid, broker_ask: broker.ask, broker_quote_timestamp: broker.observed_at, spread: detail.execution_evidence?.spread, additional_round_trip_cost: detail.execution_evidence?.additional_round_trip_cost, commission_price_cost: detail.costs?.round_trip_price_cost, price_comparison_status: discrepancy.status, absolute_mid_gap: discrepancy.absolute_mid_gap, actual_tp_fraction: discrepancy.actual_tp_fraction }} />
      <p className="journal-muted">Source timestamp proximity does not establish identical feed event times. Unknown costs remain unknown.</p>
    </section>
    <section><h3>Lifecycle timing</h3><Fields values={{ detected_at: detail.detected_at, detection_ms: detail.timing.detection_ms, strategy_evaluation_ms: detail.timing.strategy_evaluation_ms, total_observation_ms: detail.timing.total_observation_ms, ai_reasoning_ms: reasoning.duration_ms, ai_reasoning_started_at: reasoning.started_at, ai_reasoning_completed_at: reasoning.completed_at, ai_decision_stage_ms: detail.timing.ai_decision_stage_ms, risk_validation_ms: detail.timing.risk_validation_ms, order_submission_ms: detail.timing.order_submission_ms, submission_to_fill_confirmation_ms: tracking.submission_to_fill_confirmation_ms, broker_fill_event_at: tracking.broker_fill_event_at, fill_confirmation_received_at: tracking.fill_confirmation_received_at }} /><p className="journal-muted">Absent AI or fill measurements mean those timings were not recorded, not zero latency.</p></section>
    <section><h3>Linked records</h3><Fields values={{ evaluation_id: detail.id, opportunity_id: detail.opportunity_id, strategy_id: detail.strategy_id }} />
      {detail.ai_decision ? <details><summary>AI decision · {detail.decision_id}</summary><Fields values={Object.fromEntries(Object.entries(detail.ai_decision).filter(([key]) => !['market_context','macro_context'].includes(key)))} /></details> : <p className="journal-muted">No linked AI decision.</p>}
      {detail.broker_execution ? <details><summary>Broker execution · {detail.request_id}</summary><Fields values={Object.fromEntries(Object.entries(detail.broker_execution).filter(([key]) => key !== 'request_data'))} /><p className="journal-muted">Submitted is not filled. Partial fill volume and broker error details are shown when recorded.</p></details> : <p className="journal-muted">No linked broker execution.</p>}
    </section>
    <details><summary>Technical records · raw JSON</summary><pre>{JSON.stringify(detail, null, 2)}</pre></details>
  </div>;
}
function HistoryRow({ item, selected }: { item: JournalItem; selected: string | null }) {
  const [open, setOpen] = useState(selected === item.id);
  return <article className="journal-row"><button className="journal-row-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
    <span><strong>{item.symbol} · {item.direction ? label(item.direction) : 'No direction'}</strong><small>{timestamp(item.created_at)}</small></span>
    <span>{item.setup_type ? label(item.setup_type) : 'No setup identified'}<small>{item.mode === 'observation' ? 'Observation only' : 'Execution pipeline'}</small></span>
    <span className={`journal-outcome journal-${item.outcome}`}>{label(item.outcome)}</span><span aria-hidden="true">{open ? '−' : '+'}</span>
  </button>{open && <><a className="journal-permalink" href={`#journal?id=${encodeURIComponent(item.id)}`}>Link to evaluation</a><Detail id={item.id} /></>}</article>;
}
export function DecisionJournalPage() {
  const [filters, setFilters] = useState<JournalFilters>(emptyFilters);
  const [page, setPage] = useState(1);
  const [history, setHistory] = useState<JournalHistory | null>(null);
  const [summary, setSummary] = useState<JournalSummary | null>(null);
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState(() => new URLSearchParams(window.location.hash.split('?')[1]).get('id'));
  useEffect(() => { const changed = () => setSelected(new URLSearchParams(window.location.hash.split('?')[1]).get('id')); window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed); }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError('');
    let params: URLSearchParams;
    try { params = journalQuery(filters); } catch { setError('Enter valid date filters.'); setLoading(false); return; }
    Promise.all([journalGet<JournalHistory>(`evaluations?${params}&page=${page}`, controller.signal), journalGet<JournalSummary>(`summary?${params}`, controller.signal)])
      .then(([rows, stats]) => { setHistory(rows); setSummary(stats); })
      .catch(e => { if (!controller.signal.aborted) setError(String(e.message)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [filters, page, refresh]);
  function filter(key: keyof JournalFilters, value: string) { setPage(1); setFilters(f => ({ ...f, [key]: value })); }
  return <div className="journal-page"><div className="journal-heading"><div><h1>Decision Journal</h1><p>Persisted XAUUSD evidence, proposals and execution outcomes. Read only.</p></div><button onClick={() => setRefresh(r => r + 1)}>Refresh</button></div>
    <Card><div className="journal-filters"><label>From (local time)<input type="datetime-local" value={filters.since} onChange={e => filter('since', e.target.value)} /></label><label>Until (exclusive)<input type="datetime-local" value={filters.until} onChange={e => filter('until', e.target.value)} /></label>
      <label>Outcome<select value={filters.outcome} onChange={e => filter('outcome', e.target.value)}><option value="">All outcomes</option>{outcomes.map(v => <option key={v} value={v}>{label(v)}</option>)}</select></label>
      <label>Setup<select value={filters.setup} onChange={e => filter('setup', e.target.value)}><option value="">All setups</option>{setups.map(v => <option key={v} value={v}>{label(v)}</option>)}</select></label>
      <label>Direction<select value={filters.direction} onChange={e => filter('direction', e.target.value)}><option value="">All / unavailable</option><option value="long">Long</option><option value="short">Short</option></select></label><button onClick={() => { setFilters(emptyFilters); setPage(1); }}>Clear filters</button></div></Card>
    {loading && <p role="status">Loading journal…</p>}{error && <p role="alert">{error}</p>}
    {!loading && !error && summary && history && <>
      <div className="journal-metrics">{[['Evaluations',summary.total],['Eligible proposals',summary.eligible],['Strategy rejected',summary.rejected],['Incomplete',summary.incomplete],['Submitted / filled',summary.executed],['Safety / pipeline blocks',summary.blocked]].map(([title,value]) => <Card key={title}><span>{title}</span><strong>{value}</strong></Card>)}</div>
      <p className="journal-muted">{summary.observation_only} observation-only · {summary.filled} confirmed fills. {summary.count_semantics}</p>
      <Card><h2>Rejection frequency</h2>{Object.keys(summary.rejection_frequencies).length ? <ul className="journal-frequency">{Object.entries(summary.rejection_frequencies).map(([reason,count]) => <li key={reason}><span>{label(reason)}</span><strong>{count}</strong></li>)}</ul> : <p className="journal-muted">No rejection codes in this selection.</p>}</Card>
      <Card><h2>Evaluation history · newest first</h2>{!history.items.length ? <p className="journal-muted">No evaluations match these filters.</p> : history.items.map(item => <HistoryRow key={`${item.id}-${selected}`} item={item} selected={selected} />)}
      <div className="journal-pagination"><button disabled={page===1} onClick={() => setPage(p => p-1)}>Previous</button><span>Page {page} of {Math.max(1,Math.ceil(history.total/history.page_size))} · {history.total} records</span><button disabled={page*history.page_size >= history.total} onClick={() => setPage(p => p+1)}>Next</button></div></Card>
    </>}
    {selected && !history?.items.some(item => item.id === selected) && <Card><h2>Linked evaluation</h2><Detail id={selected} /></Card>}
  </div>;
}
