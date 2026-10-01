import type { LucideIcon } from 'lucide-react';
import { Card } from '../../components/ui/Card';
export function StatCard({ title, value, detail, icon: Icon, positive = false }: { title: string; value: string; detail: string; icon: LucideIcon; positive?: boolean }) {
  return <Card className="stat-card"><div className="stat-label">{title}<Icon size={17} /></div><div className={`stat-value ${positive ? 'positive' : ''}`}>{value}</div><div className="stat-detail">{positive && <span className="positive">↗ </span>}{detail}</div></Card>;
}
