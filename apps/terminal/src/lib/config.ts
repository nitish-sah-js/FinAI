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

// Shared cluster secret (X-Cluster-Key). Empty in single-laptop mode. It ends up in the app bundle, so it only
// keeps other devices on the network out; it is not a user password.
export const CLUSTER_KEY = (process.env.NEXT_PUBLIC_CLUSTER_KEY || '').trim();
/** Append ?key= to a WebSocket URL (browsers cannot set headers on a WebSocket). */
export const withKey = (url: string) => (CLUSTER_KEY ? `${url}${url.includes('?') ? '&' : '?'}key=${encodeURIComponent(CLUSTER_KEY)}` : url);
