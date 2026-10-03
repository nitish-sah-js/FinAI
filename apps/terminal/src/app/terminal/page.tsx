'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Maximize2, X, Minus, Moon, RefreshCw, Sun } from 'lucide-react';
import BrailleTerrainBackground from '@/components/BrailleTerrainBackground';
import * as api from '@/lib/api';
import type { Alert, Portfolio, ToolResult } from '@/lib/contracts';
import { useSettings } from '@/lib/store';
import { useAlerts } from '@/lib/ws';
import { Empty, PANEL, PanelHeader, fmtInr, fmtPct, type Bar } from '@/components/panels';
import { BacktestView, HealthView, PaperView, PortfolioView, SettingsView, type PriceMap } from '@/components/views';
import { HomeView, type Turn } from '@/components/home';

export default function TerminalWindow() {
  const [isLanding, setIsLanding] = useState(true);
  const [activeTab, setActiveTab] = useState('Overview');
  const theme = useSettings((s) => s.theme);
  const toggleTheme = useSettings((s) => s.toggleTheme);

  // ---- backend data
  const [portfolio, setPortfolio] = useState<Portfolio | null>(null);
  const [bars, setBars] = useState<Record<string, Bar[]>>({});
  const [backendErr, setBackendErr] = useState<string | null>(null);
  const { alerts, connected: monitorUp, markAcked } = useAlerts();

  // ---- the Home query thread (kept here so it survives tab switches)
  const [turns, setTurns] = useState<Turn[]>([]);
  const nextId = useRef(1);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.getPortfolio('demo').then(setPortfolio).catch((e) => setBackendErr(`Orchestrator unreachable (${e.message}). Start the backend: infra/run_all_local.ps1`));
  }, []);

  // prices for the ticker tape / portfolio / chart (ingestion /prices)
  const tickers = useMemo(() => portfolio?.holdings.map((h) => h.ticker) ?? [], [portfolio]);
  useEffect(() => {
    if (!tickers.length) return;
    api.getPrices(tickers).then((r: ToolResult) => {
      const m: Record<string, Bar[]> = {};
      for (const e of r.evidence) if (e.value?.ticker && Array.isArray(e.value.rows)) m[e.value.ticker] = e.value.rows;
      setBars((b) => ({ ...b, ...m }));
    }).catch(() => {});
  }, [tickers]);
  const prices: PriceMap = useMemo(() => {
    const out: PriceMap = {};
    for (const [t, rows] of Object.entries(bars)) {
      const closes = rows.map((r) => r.close).filter((c) => c != null);
      if (closes.length) out[t] = { last: closes[closes.length - 1], prev: closes.length > 1 ? closes[closes.length - 2] : null };
    }
    return out;
  }, [bars]);

  // ---- submit a query: new turn on Home (also used by deep links /run/new?q=… and alert "Analyze")
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  const submit = useCallback(async (q: string, alertId?: string | null) => {
    if (!q.trim()) return;
    setIsLanding(true);
    const id = nextId.current++;
    const prevRun = [...turnsRef.current].reverse().find((t) => t.runId)?.runId ?? undefined;
    setTurns((ts) => [...ts, { id, query: q, runId: null, error: null }]);
    setSubmitting(true);
    const s = useSettings.getState();
    try {
      const acc = await api.postQuery({
        query: q, portfolio: portfolio ?? undefined, as_of: s.asOf || undefined, llm_mode: s.llmMode, lang: s.lang,
        chaos: s.chaos, alert_id: alertId ?? undefined, ref_run_id: prevRun,   // ref_run_id: "explain that" follow-ups
      });
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, runId: acc.run_id } : t)));
    } catch (e: any) {
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: `Query failed: ${e.message}` } : t)));
    } finally {
      setSubmitting(false);
    }
  }, [portfolio]);

  const openRun = useCallback((runId: string, query: string) => {
    setIsLanding(true);
    const id = nextId.current++;
    setTurns((ts) => [...ts, { id, query, runId, error: null }]);
  }, []);

  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current) return;
    const p = new URLSearchParams(window.location.search);
    const run = p.get('run');
    const q = p.get('q');
    if (run) {
      deepLinked.current = true;
      api.getRun(run).then((r) => openRun(run, r.query)).catch(() => openRun(run, run));
    } else if (q && portfolio) { deepLinked.current = true; submit(q, p.get('alert')); }
    window.terminalApi?.onDeepLink?.((link: string) => {
      try {
        const u = new URL(link, window.location.origin);
        const lq = u.searchParams.get('q');
        if (lq) submit(lq, u.searchParams.get('alert'));
      } catch {}
    });
  }, [portfolio, submit, openRun]);

  const handleMinimize = () => window.terminalApi?.minimize();
  const handleMaximize = () => window.terminalApi?.maximize();
  const handleClose = () => window.terminalApi?.close();

  const tape = tickers.filter((t) => prices[t]).map((t) => {
    const { last, prev } = prices[t];
    return { t, ch: prev ? (last - prev) / prev : 0 };
  });

  const navTabs = ['Home', 'Overview', 'Portfolio', 'Paper', 'Backtest', 'Health', 'Settings'];
  const isActive = (tab: string) => (tab === 'Home' ? isLanding : !isLanding && activeTab === tab);
  const iconBtn = 'p-2 rounded-lg transition-colors text-t-muted hover:text-t-fg hover:bg-t-fg/[0.07] focus-visible:outline focus-visible:outline-2 focus-visible:outline-t-fg/40';

  return (
    <BrailleTerrainBackground background={theme === 'light' ? '#eef0f3' : '#050505'} color={theme === 'light' ? '#3e4a5a' : '#c3cad4'}
      blur={theme === 'light' ? 0.6 : 2} opacity={isLanding ? 1 : theme === 'light' ? 0.18 : 0.35}
      className="h-screen w-screen overflow-hidden text-t-text selection:bg-indigo-500/30">
      <div className="flex flex-col h-full w-full text-sm">
        {/* Title bar (draggable) */}
        <header className="grid grid-cols-[1fr_auto_1fr] items-center gap-4 px-5 pt-4 pb-3 z-50 shrink-0 bg-t-ink/80 backdrop-blur-sm" style={{ WebkitAppRegion: 'drag' } as any}>
          <div className="flex items-center gap-2.5">
            <span className="w-7 h-7 rounded-lg bg-t-fg text-t-ink grid place-items-center text-[15px] font-black leading-none" aria-hidden>Σ</span>
            <span className="font-bold text-t-fg text-[15px]">Sigma</span>
          </div>

          <nav className="flex items-center gap-0.5 bg-t-panel/90 backdrop-blur-xl border border-t-fg/10 rounded-xl p-1 shadow-lg shadow-black/10"
            style={{ WebkitAppRegion: 'no-drag' } as any} aria-label="Sections">
            {navTabs.map((tab) => (
              <button key={tab} aria-current={isActive(tab) ? 'page' : undefined}
                onClick={() => { if (tab === 'Home') setIsLanding(true); else { setActiveTab(tab); setIsLanding(false); } }}
                className={`relative px-3.5 py-1.5 text-[13px] rounded-lg transition-colors ${isActive(tab) ? 'bg-t-fg/[0.09] text-t-fg font-bold' : 'text-t-muted hover:text-t-fg font-medium'}`}>
                {tab}
                {tab === 'Overview' && alerts.some((a) => !a.acknowledged && a.tier >= 2) && (
                  <span className="absolute top-1.5 right-1.5 w-1.5 h-1.5 rounded-full bg-t-amber" aria-label="Unread alerts" />
                )}
              </button>
            ))}
          </nav>

          <div className="flex items-center justify-end gap-1" style={{ WebkitAppRegion: 'no-drag' } as any}>
            <span className="flex items-center gap-1.5 text-xs text-t-muted mr-2" title={backendErr ?? 'Orchestrator reachable'}>
              <span className={`w-1.5 h-1.5 rounded-full ${backendErr ? 'bg-t-rose' : 'bg-t-mint'}`} />{backendErr ? 'Backend offline' : 'Connected'}
            </span>
            <span className="flex items-center gap-1.5 text-xs text-t-muted mr-2">
              <span className={`w-1.5 h-1.5 rounded-full ${monitorUp ? 'bg-t-mint' : 'bg-t-muted/60'}`} />Alerts {monitorUp ? 'on' : 'off'}
            </span>
            <button onClick={toggleTheme} title={theme === 'light' ? 'Switch to dark theme' : 'Switch to light theme'} aria-label="Toggle theme" className={iconBtn}>
              {theme === 'light' ? <Moon size={16} /> : <Sun size={16} />}
            </button>
            <button onClick={handleMinimize} aria-label="Minimize" className={iconBtn}><Minus size={16} /></button>
            <button onClick={handleMaximize} aria-label="Maximize" className={iconBtn}><Maximize2 size={15} /></button>
            <button onClick={handleClose} aria-label="Close" className={`${iconBtn} hover:!text-t-rose hover:!bg-t-rose/10`}><X size={16} /></button>
          </div>
        </header>

        {/* Holdings strip: latest daily change from ingestion (static; no scrolling) */}
        {!isLanding && (
          <div className="mx-5 mb-1 flex items-center gap-6 overflow-hidden whitespace-nowrap text-[13px] h-8 px-4 rounded-lg bg-t-panel/70 border border-t-fg/[0.07]">
            {tape.length === 0 && <span className="text-t-muted">Loading prices…</span>}
            {tape.map((x) => (
              <span key={x.t} className="text-t-muted">{x.t.replace(/\.NS$/, '')}{' '}
                <span className={x.ch >= 0 ? 'text-t-mint' : 'text-t-rose'}>{x.ch >= 0 ? '+' : '−'}{Math.abs(x.ch * 100).toFixed(1)}%</span>
              </span>
            ))}
          </div>
        )}

        {backendErr && <div className="mx-5 mt-2 text-t-rose text-[13px] rounded-lg border border-t-rose/30 bg-t-rose/[0.06] px-4 py-2 z-20">{backendErr}</div>}

        {/* Main content */}
        <main className="flex-1 overflow-hidden relative z-10 flex flex-col">
          {isLanding ? (
            <HomeView turns={turns} nextId={nextId.current} onSubmit={submit} submitting={submitting}
              onNewChat={() => setTurns([])} portfolio={portfolio} bars={bars} prices={prices} />
          ) : (
            <div className="flex-1 px-5 pt-3 pb-6 overflow-auto custom-scrollbar relative">
              {activeTab === 'Overview' && <Overview portfolio={portfolio} prices={prices} alerts={alerts} markAcked={markAcked} onSubmit={submit} onOpenRun={openRun} />}
              {activeTab === 'Portfolio' && <PortfolioView portfolio={portfolio} prices={prices} onSaved={setPortfolio} />}
              {activeTab === 'Paper' && <PaperView />}
              {activeTab === 'Backtest' && <BacktestView />}
              {activeTab === 'Health' && <HealthView />}
              {activeTab === 'Settings' && <SettingsView />}
            </div>
          )}
        </main>
      </div>
    </BrailleTerrainBackground>
  );
}

const TIER: Record<number, { label: string; dot: string }> = {
  3: { label: 'High', dot: 'bg-t-rose' }, 2: { label: 'Medium', dot: 'bg-t-amber' }, 1: { label: 'Low', dot: 'bg-t-muted/60' },
};
const TH = 'px-4 h-9 font-medium text-xs text-t-muted';
const TD = 'px-4 py-2.5';

// Overview: the parts that don't depend on a query (portfolio, alerts, recent runs). Query panels live on Home.
function Overview({ portfolio, prices, alerts, markAcked, onSubmit, onOpenRun }: {
  portfolio: Portfolio | null; prices: PriceMap; alerts: Alert[]; markAcked: (id: string) => void;
  onSubmit: (q: string, alertId?: string | null) => void; onOpenRun: (runId: string, query: string) => void;
}) {
  const total = (portfolio?.holdings ?? []).reduce((s, h) => s + h.qty * (prices[h.ticker]?.last ?? h.avg_price ?? 0), 0);
  const [runs, setRuns] = useState<any[] | null>(null);
  const loadRuns = () => api.getRuns(15).then(setRuns).catch(() => setRuns([]));
  useEffect(() => { loadRuns(); }, []);

  return (
    <div className="grid grid-cols-12 gap-4 auto-rows-min min-w-[1100px] max-w-[1600px] mx-auto">
      <section className={`col-span-5 h-[360px] ${PANEL} flex flex-col`}>
        <PanelHeader title="Holdings" right={<span className="text-[13px] text-t-muted">Value <span className="text-t-fg font-bold">{fmtInr(total)}</span></span>} />
        <div className="flex-1 overflow-auto">
          <table className="w-full text-left border-collapse text-[13px]">
            <thead className="sticky top-0 bg-t-panel">
              <tr className="border-b border-t-fg/[0.07]"><th className={TH}>Ticker</th><th className={`${TH} text-right`}>Weight</th>
                <th className={`${TH} text-right`}>Price</th><th className={`${TH} text-right`}>1 day</th></tr>
            </thead>
            <tbody>
              {(portfolio?.holdings ?? []).map((h) => {
                const p = prices[h.ticker];
                const ch = p?.prev ? (p.last - p.prev) / p.prev : null;
                const w = total ? (h.qty * (p?.last ?? h.avg_price ?? 0)) / total : null;
                return (
                  <tr key={h.ticker} className="border-b border-t-fg/[0.05] last:border-0">
                    <td className={`${TD} font-bold text-t-fg`}>{h.ticker.replace(/\.NS$/, '')}</td>
                    <td className={`${TD} text-right text-t-text`}>{w != null ? `${(w * 100).toFixed(1)}%` : '—'}</td>
                    <td className={`${TD} text-right text-t-text`}>{p ? p.last.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : '—'}</td>
                    <td className={`${TD} text-right ${ch == null ? 'text-t-muted' : ch >= 0 ? 'text-t-mint' : 'text-t-rose'}`}>{fmtPct(ch)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>

      <section className={`col-span-7 h-[360px] ${PANEL} flex flex-col`}>
        <PanelHeader title="Recent questions" right={<button onClick={loadRuns} aria-label="Refresh" className="text-t-muted hover:text-t-fg p-1"><RefreshCw size={14} /></button>} />
        <div className="flex-1 overflow-auto">
          {!runs ? <Empty>Loading…</Empty> : runs.length === 0 ? <Empty>No questions yet. Ask one on Home.</Empty> : (
            <ul>
              {runs.map((r) => (
                <li key={r.run_id}>
                  <button onClick={() => onOpenRun(r.run_id, r.query)} className="w-full text-left grid grid-cols-[1fr_auto] gap-4 px-4 py-2.5 border-b border-t-fg/[0.05] hover:bg-t-fg/[0.03]">
                    <span className="text-[13px] text-t-text truncate">{r.query}</span>
                    <span className="text-xs text-t-muted whitespace-nowrap">
                      {r.status !== 'done' && <span className="text-t-amber mr-2">{r.status}</span>}
                      {new Date(r.created_at).toLocaleString('en-IN', { hour12: false, day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className={`col-span-12 ${PANEL} flex flex-col`}>
        <PanelHeader title="Alerts" right={<span className="text-xs text-t-muted">{alerts.filter((a) => !a.acknowledged).length} unread</span>} />
        <div className="max-h-[420px] overflow-auto">
          {alerts.length === 0 ? <Empty>No alerts. The monitor checks prices, news and weather every few minutes.</Empty> : (
            <table className="w-full text-left border-collapse text-[13px]">
              <thead className="sticky top-0 bg-t-panel">
                <tr className="border-b border-t-fg/[0.07]"><th className={`${TH} w-20`}>Time</th><th className={`${TH} w-28`}>Priority</th><th className={`${TH} w-48`}>Holdings</th><th className={TH}>What happened</th><th className={TH} /></tr>
              </thead>
              <tbody>
                {alerts.map((a) => {
                  const q = (() => { try { return new URL(a.deeplink).searchParams.get('q'); } catch { return null; } })();
                  const tier = TIER[a.tier] ?? TIER[1];
                  return (
                    <tr key={a.alert_id} className={`border-b border-t-fg/[0.05] align-top ${a.acknowledged ? 'opacity-60' : ''}`}>
                      <td className={`${TD} text-t-muted`}>{new Date(a.created_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })}</td>
                      <td className={TD}><span className="flex items-center gap-2 text-t-text"><span className={`w-2 h-2 rounded-full ${tier.dot}`} />{tier.label}</span></td>
                      <td className={`${TD} text-t-fg font-bold`}>{a.tickers.map((t) => t.replace(/\.NS$/, '')).join(', ')}</td>
                      <td className={`${TD} text-t-text leading-snug max-w-[70ch]`} title={a.reason}>{a.headline}</td>
                      <td className={`${TD} text-right whitespace-nowrap`}>
                        {q && <button onClick={() => onSubmit(q, a.alert_id)} className="text-[13px] font-medium text-t-fg border border-t-fg/20 hover:bg-t-fg/[0.06] rounded-md px-2.5 py-1 mr-2">Analyze</button>}
                        {a.acknowledged ? <span className="text-xs text-t-muted">Read</span> : (
                          <button onClick={() => api.ackAlert(a.alert_id).then(() => markAcked(a.alert_id)).catch(() => {})} className="text-[13px] text-t-muted hover:text-t-fg px-1.5 py-1">Mark read</button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
