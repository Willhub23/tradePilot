import { FlaskConical } from 'lucide-react';
import { Card } from '../../components/ui/Card';
import { FeatureIntro } from '../../components/ui/FeatureIntro';
export function StrategiesPage() { return <FeatureIntro title="Strategies" description="A home for future research and strategy evaluation."><Card className="empty-state"><FlaskConical size={32} /><h2>No strategies yet</h2><p>This frontend foundation does not include strategy creation or backtesting.</p></Card></FeatureIntro>; }
