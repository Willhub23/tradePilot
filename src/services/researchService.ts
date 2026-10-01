import type { DashboardData, ResearchService } from '../types';

// Fictional fixed snapshot. UI components depend on the service contract, not this fixture.
const snapshot: DashboardData = {
  balance: 102458.50, dailyPnl: 458.50, dailyPnlPercent: 0.45, agentStatus: 'INACTIVE',
  asOf: '2026-09-30T16:10:00Z',
  prices: [2638,2641,2639,2645,2643,2647,2644,2642,2648,2652,2649,2651,2646,2648,2654,2657,2653,2656,2651,2654,2661,2659,2663,2660,2665,2662,2658,2661,2667,2664,2668,2671,2667,2670,2666,2669,2674,2670,2672,2675,2671,2673,2670,2674,2678,2675,2677,2673,2676,2674.8].map((price, index) => ({ time: new Date(Date.UTC(2026, 8, 30, 8, index * 10)).toISOString(), price })),
  positions: [
    { id: '1', symbol: 'XAU/USD', name: 'Gold / US Dollar', side: 'Buy', lots: 0.50, entry: 2662.40, current: 2674.80, pnl: 620.00 },
    { id: '2', symbol: 'EUR/USD', name: 'Euro / US Dollar', side: 'Sell', lots: 1.00, entry: 1.11820, current: 1.11935, pnl: -115.00 },
    { id: '3', symbol: 'GBP/USD', name: 'British Pound / US Dollar', side: 'Buy', lots: 0.50, entry: 1.33860, current: 1.33767, pnl: -46.50 },
  ],
  activities: [
    { id: '1', title: 'Market review completed', description: 'Sample research summary for XAU/USD.', time: '15:42', kind: 'research' },
    { id: '2', title: 'Strategy review saved', description: 'Demo trend-following scenario reviewed.', time: '14:30', kind: 'review' },
    { id: '3', title: 'Agent set to inactive', description: 'No agents or background tasks are running.', time: '12:00', kind: 'system' },
  ],
};

export const researchService: ResearchService = {
  async getDashboard() { return structuredClone(snapshot); },
};
