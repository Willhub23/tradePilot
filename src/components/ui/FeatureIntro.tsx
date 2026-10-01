import type { ReactNode } from 'react';
export function FeatureIntro({ title, description, children }: { title: string; description: string; children: ReactNode }) {
  return <><div className="page-heading"><div><div className="eyebrow">RESEARCH WORKSPACE · DEMO</div><h1>{title}</h1><p>{description}</p></div></div>{children}</>;
}
