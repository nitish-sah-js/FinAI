// REST client for the orchestrator (L1:8000), monitor (L3:8202) and ingestion (L3:8201). docs/13 §8.
import { INGEST_URL, MONITOR_URL, ORCH_URL, QUANT_URL } from './config';
import type { Alert, Health, Portfolio, QueryAccepted, QueryRequest, ToolResult } from './contracts';

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function req<T>(url: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), init?.timeoutMs ?? 15000);
  try {
    const r = await fetch(url, { ...init, signal: ctrl.signal });
    if (!r.ok) {
      let detail = r.statusText;
      try {
        const j = await r.json();
        detail = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail ?? j);
      } catch {}
      throw new ApiError(r.status, `${r.status} ${detail}`);
    }
    return (await r.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

const post = <T>(url: string, body: unknown, timeoutMs?: number) =>
  req<T>(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), timeoutMs });

// ---- orchestrator ----
export const postQuery = (body: QueryRequest) => post<QueryAccepted>(`${ORCH_URL}/query`, body);
export const getRuns = (limit = 20) => req<any[]>(`${ORCH_URL}/runs?limit=${limit}`);
export const getRun = (id: string) => req<any>(`${ORCH_URL}/runs/${encodeURIComponent(id)}`);
export const getQuota = () => req<any>(`${ORCH_URL}/llm/quota`);
export const getWarmup = () =>
  req<{ state: 'pending' | 'warming' | 'ready' | 'failed'; models: Record<string, string> }>(`${ORCH_URL}/llm/warmup`, { timeoutMs: 4000 });
export const getHealthAll = () => req<Record<string, Health>>(`${ORCH_URL}/health/all`, { timeoutMs: 8000 });
export const getPortfolio = (id = 'demo') => req<Portfolio>(`${ORCH_URL}/portfolio/${encodeURIComponent(id)}`);
export const savePortfolio = (p: Portfolio) => post<Portfolio>(`${ORCH_URL}/portfolio`, p);
export async function uploadPortfolioCsv(file: File, portfolioId = 'demo') {
  const fd = new FormData();
  fd.append('file', file);
  return req<{ portfolio: Portfolio; warnings: string[] }>(
    `${ORCH_URL}/portfolio/upload?portfolio_id=${encodeURIComponent(portfolioId)}`,
    { method: 'POST', body: fd },
  );
}
export const postScenario = (body: { portfolio?: Portfolio; portfolio_id?: string; shocks: Record<string, number> }) =>
  post<ToolResult>(`${ORCH_URL}/tools/scenario`, body, 15000);
export const getScoreboard = () => req<any>(`${ORCH_URL}/backtest/scoreboard`);

// ---- paper trading (doc 12 §B3) ----
export const paperPropose = (run_id: string, hedge_id: string) =>
  post<any>(`${ORCH_URL}/paper/propose`, { run_id, hedge_id });
export const paperApprove = (proposal_id: string, decision: 'approve' | 'reject', approved_by: string) =>
  post<any>(`${ORCH_URL}/paper/approve`, { proposal_id, decision, approved_by }, 30000);
export const paperPositions = (status = 'open') => req<any[]>(`${ORCH_URL}/paper/positions?status=${status}`);
export const paperHistory = () => req<{ proposals: any[]; positions: any[]; marks: any[] }>(`${ORCH_URL}/paper/history`);
export const paperMark = () => post<any>(`${ORCH_URL}/paper/mark`, {}, 60000);
export const paperClose = (position_id: string) => post<any>(`${ORCH_URL}/paper/close`, { position_id }, 30000);

// ---- monitor ----
export const getAlerts = () => req<Alert[]>(`${MONITOR_URL}/alerts`);
export const ackAlert = (id: string) => post<Alert>(`${MONITOR_URL}/alerts/${encodeURIComponent(id)}/ack`, {});

// ---- ingestion ----
export const getPrices = (tickers: string[], period = '3mo') =>
  post<ToolResult>(`${INGEST_URL}/prices`, { tickers, period, interval: '1d' }, 20000);

// ---- quant (direct, optional: docs/13 §8) ----
export const postExposure = (portfolio: Portfolio) =>
  post<ToolResult>(`${QUANT_URL}/exposure`, { portfolio }, 20000);
