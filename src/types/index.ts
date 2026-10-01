export type Page = 'dashboard' | 'markets' | 'strategies' | 'agent' | 'settings';
export interface PricePoint { time: string; price: number }
export interface Position { id: string; symbol: string; name: string; side: 'Buy' | 'Sell'; lots: number; entry: number; current: number; pnl: number }
export interface AgentActivity { id: string; title: string; description: string; time: string; kind: 'research' | 'system' | 'review' }
export interface DashboardData {
  balance: number; dailyPnl: number; dailyPnlPercent: number; agentStatus: 'INACTIVE';
  asOf: string; prices: PricePoint[]; positions: Position[]; activities: AgentActivity[];
}
export interface ResearchService { getDashboard(): Promise<DashboardData> }
