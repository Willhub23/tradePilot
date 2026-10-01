import { useEffect, useState } from 'react';
import { researchService } from '../../services/researchService';
import type { DashboardData } from '../../types';
export function useDashboard() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    researchService.getDashboard().then(result => { if (!cancelled) setData(result); }).catch(() => { if (!cancelled) setError('Unable to load demo data. Please reload to try again.'); });
    return () => { cancelled = true; };
  }, []);
  return { data, error };
}
