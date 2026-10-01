import { Activity, ArrowUpRight, Bot, ChartNoAxesCombined, ChevronRight, FlaskConical, LayoutDashboard, Settings2, ShieldCheck } from 'lucide-react';
import type { ReactNode } from 'react';
import type { Page } from '../../types';
import { Badge } from '../ui/Badge';

const navigation = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard }, { id: 'markets', label: 'Markets', icon: ChartNoAxesCombined },
  { id: 'strategies', label: 'Strategies', icon: FlaskConical }, { id: 'agent', label: 'Agent', icon: Bot }, { id: 'settings', label: 'Settings', icon: Settings2 },
] as const;
export function AppLayout({ page, onNavigate, children }: { page: Page; onNavigate: (page: Page) => void; children: ReactNode }) {
  return <div className="app-shell">
    <aside className="sidebar">
      <a href="#dashboard" className="brand" onClick={() => onNavigate('dashboard')}><span className="brand-mark"><Activity size={23} /></span>TradePilot<span className="brand-dot">.</span></a>
      <div className="workspace-label">WORKSPACE</div>
      <nav aria-label="Main navigation">{navigation.map(({ id, label, icon: Icon }) => <a key={id} href={`#${id}`} aria-current={page === id ? 'page' : undefined} onClick={() => onNavigate(id)} className={`nav-item ${page === id ? 'active' : ''}`}><Icon size={18} /><span>{label}</span>{page === id && <ChevronRight size={15} className="nav-chevron" />}</a>)}</nav>
      <div className="sidebar-bottom"><div className="sandbox-card"><ShieldCheck size={20} /><strong>A space to explore</strong><p>Research, test, and learn with fictional market data.</p><Badge tone="green">PAPER TRADING</Badge></div><div className="workspace-footer"><span className="avatar">TP</span><div><strong>Research workspace</strong><small>Local demo · v0.1</small></div><ArrowUpRight size={15} /></div></div>
    </aside>
    <div className="main-shell"><header className="topbar"><div className="breadcrumb">Workspace <ChevronRight size={14} /><span>{navigation.find(item => item.id === page)?.label}</span></div><div className="header-status"><span className="demo-label"><i />Demo environment</span><Badge tone="green">PAPER TRADING</Badge></div></header><main id="main-content">{children}</main><footer className="page-footer"><span>TradePilot <span className="footer-divider">/</span> Built for thoughtful research.</span><span>Mock data only · No broker connected</span></footer></div>
  </div>;
}
