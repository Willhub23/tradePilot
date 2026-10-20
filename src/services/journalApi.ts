export type Values = Record<string, unknown>;
export interface Reason { code: string; message: string; measured?: number | string | null; limit?: number | string | null }
export interface Signal { name: string; values: Values }
export interface JournalItem {
  id: string; created_at: string; updated_at: string; strategy_id: string; symbol: string;
  mode: string; status: string; outcome: string; strategy_outcome: string | null;
  setup_type: string | null; direction: string | null; reference_price: number | null;
  market_timestamp: string | null; detected_at: string | null; opportunity_id: string | null;
  decision_id: string | null; request_id: string | null; decision_reason: string | null;
  block_reason: string | null; rejection_reasons: Reason[]; proposal: Values | null;
}
export interface JournalDetail extends JournalItem {
  supporting_signals: Signal[]; conflicting_signals: Signal[]; invalidation_condition: string | null;
  market_conditions: Values | null; execution_evidence: Values | null; broker_snapshot: Values | null;
  costs: Values | null; price_consistency: Values | null; missing_inputs: string[];
  input_error: string | null; timing: Values; ai_decision: Values | null; broker_execution: Values | null; raw_data: Values;
}
export interface JournalHistory { items: JournalItem[]; total: number; page: number; page_size: number }
export interface JournalSummary {
  total: number; eligible: number; rejected: number; incomplete: number; executed: number;
  filled: number; blocked: number; observation_only: number; outcomes: Record<string, number>;
  rejection_frequencies: Record<string, number>; count_semantics: string;
}
export interface JournalFilters { since: string; until: string; outcome: string; setup: string; direction: string }
export function journalQuery(filters: JournalFilters): URLSearchParams {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value) params.set(key, key === 'since' || key === 'until' ? new Date(value).toISOString() : value);
  }
  return params;
}
export async function journalGet<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`/api/journal/${path}`, { signal });
  if (!response.ok) throw new Error(`Journal request failed (${response.status}). Check the read-only API connection and filters.`);
  return response.json() as Promise<T>;
}
