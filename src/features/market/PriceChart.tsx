import { useId } from 'react';
import { ArrowUpRight } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { Badge } from '../../components/ui/Badge';
import type { PricePoint } from '../../types';
export function PriceChart({ prices }: { prices: PricePoint[] }) {
  const gradientId = useId();
  const first = prices[0]; const last = prices[prices.length - 1];
  if (!first || !last) return <Card><p>No price history available.</p></Card>;
  const min = Math.floor(Math.min(...prices.map(p => p.price)) / 10) * 10 - 10;
  const max = Math.ceil(Math.max(...prices.map(p => p.price)) / 10) * 10 + 10;
  const y = (price: number) => 215 - (price - min) / (max - min) * 200;
  const points = prices.map((p, i) => `${i / Math.max(1, prices.length - 1) * 700},${y(p.price)}`).join(' ');
  const change = last.price - first.price;
  return <Card className="chart-card"><div className="panel-heading"><div><h2>Market overview</h2><p>A closer look at your watchlist</p></div><Badge>MOCK DATA</Badge></div>
    <div className="chart-summary"><div className="instrument"><span className="gold-symbol">Au</span><div><strong>XAU/USD</strong><small>Gold / US Dollar</small></div></div><span className="chart-period">30 SEP 2026 · INTRADAY</span></div>
    <div className="price-line"><strong>{last.price.toLocaleString('en-US', { minimumFractionDigits: 2 })}</strong><span className="currency">USD</span><span className={change >= 0 ? 'positive' : 'negative'}><ArrowUpRight size={14} /> {change >= 0 ? '+' : ''}{change.toFixed(2)} ({(change / first.price * 100).toFixed(2)}%)</span></div>
    <div className="chart-wrap"><svg viewBox="0 0 770 260" role="img" aria-label={`Fictional XAU/USD intraday price chart, from ${first.price} to ${last.price} US dollars.`}>
      <defs><linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#54cbb0" stopOpacity="0.18" /><stop offset="100%" stopColor="#54cbb0" stopOpacity="0" /></linearGradient></defs>
      {[0,1,2,3,4].map(i => { const value = max - (max - min) * i / 4; return <g key={i}><line x1="0" x2="705" y1={y(value)} y2={y(value)} stroke="#252b34" strokeDasharray="3 5" /><text x="720" y={y(value) + 4} fill="#77818f" fontSize="11">{value.toLocaleString('en-US')}</text></g>; })}
      <polygon points={`0,220 ${points} 700,220`} fill={`url(#${gradientId})`} /><polyline points={points} fill="none" stroke="#63d2b7" strokeWidth="2.4" strokeLinejoin="round" /><circle cx="700" cy={y(last.price)} r="4" fill="#63d2b7" />
      {['08:00', '10:00', '12:00', '14:00', '16:10'].map((time, i) => <text key={time} x={i * 175} y="250" textAnchor={i === 0 ? 'start' : i === 4 ? 'end' : 'middle'} fill="#77818f" fontSize="11">{time}</text>)}
    </svg></div><div className="chart-caption"><span><i className="legend-dot" /> Simulated price history</span><span>Time in UTC</span></div>
  </Card>;
}
