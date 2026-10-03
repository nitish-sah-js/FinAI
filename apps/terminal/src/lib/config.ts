// Backend URLs (docs/13 §3). Override in apps/terminal/.env.local; defaults = single-laptop mode.
// Use 127.0.0.1, not localhost: another app may hold :8000 on IPv6 (infra/README "Gotchas").
const env = (v: string | undefined, d: string) => (v && v.trim() ? v.trim().replace(/\/$/, '') : d);

export const ORCH_URL = env(process.env.NEXT_PUBLIC_ORCH_URL, 'http://127.0.0.1:8000');
export const ORCH_WS = env(process.env.NEXT_PUBLIC_ORCH_WS, ORCH_URL.replace(/^http/, 'ws'));
export const MONITOR_URL = env(process.env.NEXT_PUBLIC_MONITOR_URL, 'http://127.0.0.1:8202');
export const MONITOR_WS = env(process.env.NEXT_PUBLIC_MONITOR_WS, MONITOR_URL.replace(/^http/, 'ws'));
export const QUANT_URL = env(process.env.NEXT_PUBLIC_QUANT_URL, 'http://127.0.0.1:8101');
export const INGEST_URL = env(process.env.NEXT_PUBLIC_INGEST_URL, 'http://127.0.0.1:8201');
export const VECTOR_URL = env(process.env.NEXT_PUBLIC_VECTOR_URL, 'http://127.0.0.1:8104');
