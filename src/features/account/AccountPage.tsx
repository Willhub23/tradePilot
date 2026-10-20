import { useEffect,useState } from 'react';
import { Card } from '../../components/ui/Card';
import { accountGet,accountPeriod,amount } from '../../services/accountApi';
import type { AccountOverview,AccountPositions,AccountTrades,AccountPerformance,AccountTrade } from '../../services/accountApi';

function localDate(date:Date) { const d=new Date(date.getTime()-date.getTimezoneOffset()*60000);return d.toISOString().slice(0,16); }
function when(value:string|null) { return value ? new Date(value).toLocaleString() : 'Unavailable'; }
function ClosedDeal({trade,currency}:{trade:AccountTrade;currency:string|null}) {
  return <details className="account-deal"><summary><span><strong>{trade.symbol || `Symbol ${trade.symbol_id}`}</strong> · position {trade.position_id}<small>{when(trade.closed_at)} · closing {trade.closing_side}</small></span><span>{trade.volume_units ?? 'Unknown'} units</span><strong>{amount(trade.net_after_reported_costs,currency)}</strong></summary>
    <dl className="journal-fields"><div><dt>Deal / order</dt><dd>{trade.deal_id} / {trade.order_id}</dd></div><div><dt>Entry / exit</dt><dd>{trade.entry_price} / {trade.exit_price ?? 'Unavailable'}</dd></div><div><dt>Gross realized P&L</dt><dd>{amount(trade.gross_profit,currency)}</dd></div><div><dt>Commission (broker value)</dt><dd>{amount(trade.commission,currency)}</dd></div><div><dt>Swap (signed)</dt><dd>{amount(trade.swap,currency)}</dd></div><div><dt>Conversion fee</dt><dd>{amount(trade.conversion_fee,currency)}</dd></div></dl>
    {!trade.costs_complete && <p className="journal-muted">Optional costs were not fully reported. The displayed net includes available costs only.</p>}
    {trade.linkage.length ? <div><p>Matching persisted position/order IDs; an unverified account match is not attribution. A position link does not prove who initiated this closing deal.</p>{trade.linkage.map(link=><p key={link.request_id}>Execution {link.request_id} · {link.match} ID match · {link.account_verified ? 'account verified' : 'account scope unavailable'}{link.decision_id && <> · AI decision {link.decision_id}</>}{link.evaluation_id && <> · <a href={`#journal?id=${encodeURIComponent(link.evaluation_id)}`}>Open Decision Journal evaluation</a></>}</p>)}</div> : <p className="journal-muted">Broker-only activity — no verified TradePilot link. Manual or historical activity is not attributed by inference.</p>}
  </details>;
}
export function AccountPage() {
  const [range,setRange]=useState(()=>{const end=new Date();return {since:localDate(new Date(end.getTime()-30*86400000)),until:localDate(end)};});
  const [applied,setApplied]=useState(range);
  const [page,setPage]=useState(1);
  const [refresh,setRefresh]=useState(0);
  const [data,setData]=useState<{overview:AccountOverview;positions:AccountPositions;trades:AccountTrades;performance:AccountPerformance}|null>(null);
  const [error,setError]=useState('');const [loading,setLoading]=useState(true);
  useEffect(()=>{
    const controller=new AbortController();setLoading(true);setError('');setData(null);
    let params:URLSearchParams;
    try { params=accountPeriod(applied.since,applied.until); } catch(e) { setError((e as Error).message);setLoading(false);return; }
    Promise.all([accountGet<AccountOverview>('overview',controller.signal),accountGet<AccountPositions>('positions',controller.signal),accountGet<AccountTrades>(`trades?${params}&page=${page}`,controller.signal),accountGet<AccountPerformance>(`performance?${params}`,controller.signal)])
      .then(([overview,positions,trades,performance])=>setData({overview,positions,trades,performance}))
      .catch(e=>{if(!controller.signal.aborted)setError(e.message);})
      .finally(()=>{if(!controller.signal.aborted)setLoading(false);});
    return ()=>controller.abort();
  },[applied,page,refresh]);
  return <div className="journal-page account-page"><div className="journal-heading"><div><h1>Broker Account</h1><p>cTrader DEMO account data · read only · separate from strategy evaluations.</p></div><button disabled={loading} onClick={()=>setRefresh(r=>r+1)}>Refresh account</button></div>
    <Card><form className="journal-filters" onSubmit={e=>{e.preventDefault();setApplied({...range});setPage(1);}}><label>Performance from (local time)<input required type="datetime-local" value={range.since} onChange={e=>setRange(r=>({...r,since:e.target.value}))}/></label><label>Until (exclusive)<input required type="datetime-local" value={range.until} onChange={e=>setRange(r=>({...r,until:e.target.value}))}/></label><button type="submit">Apply range</button></form><p className="journal-muted">Up to 90 days. Refresh updates current account data; performance remains scoped to the selected window.</p></Card>
    {loading && <p role="status">Retrieving broker-confirmed account data…</p>}{error && <Card><p role="alert">{error}</p><p className="journal-muted">No stale values shown. Use the opt-in read-only account server and ensure no trading runtime owns its broker connection.</p></Card>}
    {data && !loading && !error && <>
      <Card><h2>DEMO account {data.overview.account_id} · {data.overview.currency || 'Currency unavailable'}</h2><p className="journal-muted">Account snapshot received {when(data.overview.retrieved_at)}{data.overview.cached ? ' · cached (maximum 30 seconds)' : ''}. Receipt time is not a broker market-event timestamp. Values do not stream automatically.</p>
        {Object.entries(data.overview.unavailable).map(([field,reason])=><p key={field} className="journal-muted">{field}: {reason}</p>)}</Card>
      <div className="journal-metrics">{[['Balance',amount(data.overview.balance,data.overview.currency)],['Equity (derived when verified)',amount(data.overview.equity,data.overview.currency)],['Net unrealized P&L',amount(data.overview.unrealized_pnl,data.overview.currency)]].map(([title,value])=><Card key={title}><span>{title}</span><strong>{value}</strong></Card>)}</div>
      <Card><h2>Open broker positions · {data.positions.items.length}</h2><p className="journal-muted">Snapshot received {when(data.positions.retrieved_at)}. Volume is in asset units, not lots.</p>{!data.positions.items.length ? <p>No open positions reported.</p> : <div className="account-table-wrap"><table><thead><tr><th>Position</th><th>Symbol / side</th><th>Units</th><th>Entry</th><th>SL / TP</th><th>Opened</th></tr></thead><tbody>{data.positions.items.map(p=><tr key={p.position_id}><td>{p.position_id}</td><td>{p.symbol || p.symbol_id} · {p.direction}</td><td>{p.volume_units}</td><td>{p.entry_price ?? 'Unavailable'}</td><td>{p.stop_loss ?? 'Unavailable'} / {p.take_profit ?? 'Unavailable'}</td><td>{when(p.opened_at)}</td></tr>)}</tbody></table></div>}</Card>
      <Card><h2>Realized performance · {when(data.performance.since)} to {when(data.performance.until)}</h2><p className="journal-muted">{data.performance.basis}. {data.performance.closing_deals} closing deals across {data.performance.positions_with_realizations} positions.</p>
        <div className="journal-metrics">{[['Net after reported costs',amount(data.performance.realized_net,data.performance.currency)],['Win rate',data.performance.win_rate==null?'Unavailable':`${data.performance.win_rate.toFixed(1)}%`],['Average win',amount(data.performance.average_win,data.performance.currency)],['Average loss',amount(data.performance.average_loss,data.performance.currency)],['Profit factor',data.performance.profit_factor ?? 'Unavailable']].map(([title,value])=><div key={title}><span>{title}</span><strong>{value}</strong></div>)}</div>
        <p className="journal-muted">{!data.performance.costs_complete && 'Some optional fees were not reported; results exclude unreported costs. '}{data.performance.unavailable_reason}</p></Card>
      <Card><h2>Recent realized broker deals</h2><p className="journal-muted">{data.trades.basis}. {data.trades.cost_note}</p>{!data.trades.items.length ? <p>No closing deals in this time range.</p> : data.trades.items.map(t=><ClosedDeal key={t.deal_id} trade={t} currency={data.trades.currency}/>)}
        <div className="journal-pagination"><button disabled={page===1} onClick={()=>setPage(p=>p-1)}>Previous</button><span>Page {page} of {Math.max(1,Math.ceil(data.trades.total/data.trades.page_size))} · {data.trades.total} closing deals</span><button disabled={page*data.trades.page_size>=data.trades.total} onClick={()=>setPage(p=>p+1)}>Next</button></div></Card>
    </>}
  </div>;
}
