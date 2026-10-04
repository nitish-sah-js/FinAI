// TypeScript mirror of packages/copilot_common/copilot_common/models.py (docs/01 §5, §9).

export interface Evidence {
  id: string;
  run_id?: string | null;
  tool: string;
  value: Record<string, any>;
  summary?: string | null;
  source: string;
  source_url?: string | null;
  as_of: string;
  timestamp: string;
  freshness_s?: number | null;
  confidence?: number | null;
  degraded: boolean;
  degraded_reason?: string | null;
  latency_ms?: number | null;
  model_version?: string | null;
  staleness_factor?: number | null;
  fixture?: boolean;     // canned test data (MOCK mode only)
  synthetic?: boolean;   // demo data, not real (DEMO_MODE)
}

export interface ToolResult {
  evidence: Evidence[];
  warnings: string[];
}

export interface Holding {
  ticker: string;
  qty: number;
  avg_price?: number | null;
  sector?: string | null;
}

export interface Portfolio {
  portfolio_id: string;
  holdings: Holding[];
  cash: number;
  currency: 'INR' | 'USD';
}

export interface ChaosFlags {
  weather_down: boolean;
  force_rate_limit: boolean;
  agri_raster_missing: boolean;
  vector_down: boolean;
  slow_network_ms: number;
}

export type LlmMode = 'local' | 'boost' | 'auto';
export type Lang = 'en' | 'hi' | 'hinglish';

export interface QueryRequest {
  query: string;
  portfolio?: Portfolio | null;
  as_of?: string | null;
  llm_mode?: LlmMode | null;
  lang: Lang;
  chaos: ChaosFlags;
  ref_run_id?: string | null;
  alert_id?: string | null;
}

export interface QueryAccepted {
  run_id: string;
  ws_url: string;
}

export interface Intent {
  intent: 'event_impact' | 'portfolio_risk' | 'hedge_request' | 'explain' | 'market_summary'
    | 'stock_lookup' | 'rank_exposure' | 'what_if'
    | 'greeting' | 'smalltalk' | 'thanks' | 'help' | 'out_of_scope' | 'unclear';
  event_type?: string | null;
  region?: string | null;
  tickers: string[];
  asset_classes: string[];
  horizon_days: number;
  references_portfolio: boolean;
  needs_tools: string[];
}

export interface AgentSignal {
  agent: string;
  signal: 'bullish' | 'bearish' | 'neutral' | 'mixed' | 'n/a';
  summary: string;
  evidence_ids: string[];
  confidence?: number | null;
  degraded: boolean;
}

export interface HedgeProposal {
  hedge_id: string;
  instrument: string;
  underlying: string;
  side: 'buy' | 'sell';
  quantity: number;
  unit: 'lots' | 'shares' | 'notional_inr';
  hedge_ratio: number;
  est_cost_inr?: number | null;
  rationale: string;
  sizing_method: string;
  evidence_ids: string[];
}

export interface RedTeamReport {
  reasons: string[];
  verdict: 'proceed' | 'proceed with caution' | 'do not act';
  verdict_reason: string;
}

export interface ValidatorReport {
  numbers_found: number;
  numbers_matched: number;
  unmatched: string[];
  action: 'pass' | 'flagged' | 'stripped';
  auto_cited: string[];
  rejected_evidence?: string[];
}

export interface FinalAnswer {
  run_id: string;
  query: string;
  intent: Intent;
  bottom_line: string;
  holdings_impact: Record<string, any>[];
  hedges: HedgeProposal[];
  confidence: 'low' | 'medium' | 'high';
  what_could_be_wrong: string[];
  red_team: RedTeamReport | null;
  validator: ValidatorReport;
  signals: AgentSignal[];
  evidence: Evidence[];
  answer_markdown: string;
  lang: string;
  llm_usage: Record<string, any>;
  latency_ms: Record<string, number>;
  kind?: 'analysis' | 'conversation';   // conversation: greeting / help / unclear ... no agents, no numbers
  suggestions?: string[];
}

export type AgentStatus = 'queued' | 'started' | 'progress' | 'finished' | 'failed' | 'skipped' | 'degraded';

export interface AgentEvent {
  run_id: string;
  seq: number;
  node: string;
  status: AgentStatus;
  ts: string;
  latency_ms?: number | null;
  t_ms?: number | null;   // ms since the run started
  evidence_ids: string[];
  message?: string | null;
  model?: string | null;
  provider?: string | null;
  host?: string | null;
  tokens_in?: number | null;
  tokens_out?: number | null;
  meta: Record<string, any>;
}

export interface Alert {
  alert_id: string;
  tier: 1 | 2 | 3;
  kind: string;
  tickers: string[];
  headline: string;
  reason: string;
  impact_score: number;
  confidence: number;
  evidence_ids: string[];
  created_at: string;
  cooldown_key: string;
  deeplink: string;
  acknowledged: boolean;
}

export interface Health {
  service: string;
  status: 'ok' | 'degraded' | 'down';
  version?: string;
  mock?: boolean;
  host?: string | null;
  uptime_s?: number;
  deps?: Record<string, string>;
  models?: string[];
  laptop?: string;
  url?: string;
  error?: string;
  hint?: string;
}

export type WsMessage = { type: 'event'; data: AgentEvent } | { type: 'final'; data: FinalAnswer }
  | { type: 'pet_reaction'; data: { reaction: PetReaction; run_id?: string } };

export type PetReaction = 'wave' | 'think' | 'confused' | 'happy' | 'alert';
