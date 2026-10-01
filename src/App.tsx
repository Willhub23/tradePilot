import { useEffect, useState } from 'react';
import { AppLayout } from './components/layout/AppLayout';
import { DashboardPage } from './features/dashboard/DashboardPage';
import { useDashboard } from './features/dashboard/useDashboard';
import { MarketsPage } from './features/market/MarketsPage';
import { StrategiesPage } from './features/strategies/StrategiesPage';
import { AgentPage } from './features/agent/AgentPage';
import { SettingsPage } from './features/settings/SettingsPage';
import type { Page } from './types';
function currentPage(): Page { const hash = window.location.hash.slice(1); return ['dashboard','markets','strategies','agent','settings'].includes(hash) ? hash as Page : 'dashboard'; }
export default function App() {
  const [page, setPage] = useState<Page>(currentPage);
  const { data, error } = useDashboard();
  useEffect(() => { const update = () => setPage(currentPage()); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  return <AppLayout page={page} onNavigate={setPage}>{error ? <p role="alert">{error}</p> : !data ? <p role="status">Loading demo workspace…</p> : page === 'dashboard' ? <DashboardPage data={data} /> : page === 'markets' ? <MarketsPage prices={data.prices} /> : page === 'strategies' ? <StrategiesPage /> : page === 'agent' ? <AgentPage activities={data.activities} /> : <SettingsPage />}</AppLayout>;
}
