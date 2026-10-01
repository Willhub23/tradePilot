import { Wallet, TrendingUp, Layers3, Bot, Info } from 'lucide-react';
import { StatCard } from './StatCard';
import { PriceChart } from '../market/PriceChart';
import { PositionsTable } from '../trading/PositionsTable';
import { AgentActivity } from '../agent/AgentActivity';
import { money, signedMoney } from '../../components/ui/format';
import type { DashboardData } from '../../types';
export function DashboardPage({ data }: { data: DashboardData }) {
  return <><div className="page-heading"><div><div className="eyebrow">YOUR RESEARCH, AT A GLANCE</div><h1>Dashboard</h1><p>A clear view of your paper trading workspace.</p></div><div className="snapshot-label"><span className="status-dot" />Fictional snapshot<small>30 Sep 2026 · UTC</small></div></div><div className="demo-banner"><Info size={16} /><span><strong>You’re in a demo workspace.</strong> All balances, prices, positions, and activity are fictional. No live market data or trading.</span></div><div className="stats-grid"><StatCard title="Portfolio balance" value={money(data.balance)} detail="USD · Simulated account balance" icon={Wallet} /><StatCard title="Daily P/L" value={signedMoney(data.dailyPnl)} detail={`+${data.dailyPnlPercent.toFixed(2)}% · Fictional daily performance`} icon={TrendingUp} positive /><StatCard title="Active positions" value={String(data.positions.length).padStart(2, '0')} detail="Across 3 instruments · Paper only" icon={Layers3} /><StatCard title="Agent status" value={data.agentStatus} detail="No active research tasks" icon={Bot} /></div><div className="dashboard-grid"><PriceChart prices={data.prices} /><AgentActivity activities={data.activities} /></div><PositionsTable positions={data.positions} /></>;
}
