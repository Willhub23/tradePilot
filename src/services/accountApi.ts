export interface AccountEnvelope { account_id: string; environment: 'demo'; retrieved_at: string; cached: boolean; age_seconds: number; timestamp_semantics: string }
export interface AccountOverview extends AccountEnvelope { currency: string | null; balance: string | null; equity: string | null; unrealized_pnl: string | null; equity_source: string | null; unavailable: Record<string,string> }
export interface AccountPosition { position_id: string; symbol: string | null; symbol_id: string; direction: string; volume_units: string; volume_lots: string | null; entry_price: number | null; stop_loss: number | null; take_profit: number | null; opened_at: string | null; swap: string | null; commission: string | null }
export interface AccountPositions extends AccountEnvelope { items: AccountPosition[] }
export interface AccountLink { request_id: string; decision_id: string | null; evaluation_id: string | null; match: string; account_verified: boolean }
export interface AccountTrade { deal_id: string; order_id: string; position_id: string; symbol: string | null; symbol_id: string; closing_side: string; volume_units: string | null; filled_volume_units: string; closed_at: string; entry_price: number; exit_price: number | null; gross_profit: string | null; swap: string | null; commission: string | null; conversion_fee: string | null; net_after_reported_costs: string | null; costs_complete: boolean; linkage: AccountLink[]; attribution: string }
export interface AccountTrades extends AccountEnvelope { items: AccountTrade[]; total: number; page: number; page_size: number; currency: string | null; since: string; until: string; basis: string; cost_note: string }
export interface AccountPerformance extends AccountEnvelope { currency: string | null; since: string; until: string; basis: string; realized_net: string | null; positions_with_realizations: number; closing_deals: number; win_rate: number | null; average_win: string | null; average_loss: string | null; profit_factor: string | null; costs_complete: boolean; unavailable_reason: string }
export function amount(value: string | null | undefined, currency: string | null): string {
  return value == null ? 'Unavailable' : `${value} ${currency || '(currency unavailable)'}`;
}
export function accountPeriod(since: string, until: string): URLSearchParams {
  const start=new Date(since), end=new Date(until);
  if (!Number.isFinite(start.getTime()) || !Number.isFinite(end.getTime()) || end<=start || end.getTime()-start.getTime()>90*86400000) throw new Error('Choose an increasing date range of at most 90 days.');
  return new URLSearchParams({ since:start.toISOString(),until:end.toISOString() });
}
export async function accountGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response=await fetch(`/api/account/${path}`,{signal});
  if (!response.ok) {
    const body=await response.json().catch(()=>null);
    throw new Error(typeof body?.detail==='string' ? body.detail : `Account data unavailable (${response.status})`);
  }
  return response.json() as Promise<T>;
}
