import { Bot, FileText, ScanLine, Power } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { Badge } from '../../components/ui/Badge';
import type { AgentActivity as Activity } from '../../types';
export function AgentActivity({ activities }: { activities: Activity[] }) {
  return <Card className="activity-card"><div className="panel-heading"><h2>Agent activity</h2><Bot size={18} className="muted" /></div><div className="agent-summary"><span className="agent-icon"><Bot size={22} /></span><div><strong>Research agent</strong><small>Ready when you are</small></div><Badge>INACTIVE</Badge></div><p className="activity-note">Example activity from a fictional session.</p><ol className="activity-list">{activities.map(activity => { const Icon = activity.kind === 'research' ? ScanLine : activity.kind === 'review' ? FileText : Power; return <li key={activity.id}><span className="timeline-icon"><Icon size={15} /></span><div><div className="activity-title"><strong>{activity.title}</strong><time>{activity.time}</time></div><p>{activity.description}</p></div></li>; })}</ol><div className="agent-footnote"><span className="status-dot" /> No autonomous tasks running</div></Card>;
}
