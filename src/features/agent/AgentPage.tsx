import { FeatureIntro } from '../../components/ui/FeatureIntro';
import { AgentActivity } from './AgentActivity';
import type { AgentActivity as Activity } from '../../types';
export function AgentPage({ activities }: { activities: Activity[] }) { return <FeatureIntro title="Agent" description="The research agent is inactive. No AI integrations or autonomous execution are configured."><AgentActivity activities={activities} /></FeatureIntro>; }
