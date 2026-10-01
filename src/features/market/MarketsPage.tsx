import { FeatureIntro } from '../../components/ui/FeatureIntro';
import { PriceChart } from './PriceChart';
import type { PricePoint } from '../../types';
export function MarketsPage({ prices }: { prices: PricePoint[] }) { return <FeatureIntro title="Markets" description="Explore a fictional gold price snapshot. Live data providers are not connected."><PriceChart prices={prices} /></FeatureIntro>; }
