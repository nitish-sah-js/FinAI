'use client';
// Secondary tabs (docs/13 Prompt 13-D): Portfolio, Paper, Backtest, Health, Settings — all backed by the orchestrator.
import React, { useCallback, useEffect, useState } from 'react';
import { Moon, RefreshCw, Sun, Trash2, Upload } from 'lucide-react';
import { ResponsiveContainer, Scatter, XAxis, YAxis, Tooltip, Line, ComposedChart, CartesianGrid } from 'recharts';
import * as api from '@/lib/api';
import type { Health, Portfolio } from '@/lib/contracts';
import { useSettings } from '@/lib/store';
import { FRIENDLY } from '@/lib/demo';
import { Empty, fmtInr, fmtPct, PANEL, PanelHeader } from './panels';

export type PriceMap = Record<string, { last: number; prev: number | null }>;

// shared styles for these tabs
const TH = 'px-4 h-9 font-medium text-xs text-t-muted whitespace-nowrap';
const TD = 'px-4 py-2.5';
const BTN_PRIMARY = 'bg-t-fg text-t-ink hover:bg-t-fg/85 font-bold rounded-lg px-3.5 py-1.5 text-[13px] transition-colors disabled:opacity-50';
const BTN = 'text-t-fg border border-t-fg/20 hover:bg-t-fg/[0.06] font-medium rounded-lg px-3 py-1.5 text-[13px] transition-colors disabled:opacity-50 inline-flex items-center gap-1.5';
const fmtTs = (iso?: string | null) =>
  iso ? new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hour12: false }) : '—';
const pnlTone = (v: number | null | undefined) => (v == null ? 'text-t-muted' : v >= 0 ? 'text-t-mint' : 'text-t-rose');

function Notice({ tone, children }: { tone: 'error' | 'ok' | 'warn'; children: React.ReactNode }) {
  const c = { error: 'border-t-rose/30 bg-t-rose/[0.06] text-t-rose', ok: 'border-t-mint/30 bg-t-mint/[0.06] text-t-text', warn: 'border-t-amber/30 bg-t-amber/[0.06] text-t-text' }[tone];
  return <div className={`rounded-lg border px-4 py-2.5 text-[13px] ${c}`}>{children}</div>;
}

// ---------------------------------------------------------------- Portfolio
export function PortfolioView({ portfolio, prices, onSaved }: { portfolio: Portfolio | null; prices: PriceMap; onSaved: (p: Portfolio) => void }) {
  const [rows, setRows] = useState(portfolio?.holdings ?? []);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => setRows(portfolio?.holdings ?? []), [portfolio]);

  const total = rows.reduce((s, h) => s + h.qty * (prices[h.ticker]?.last ?? h.avg_price ?? 0), 0);
  const pnl = rows.reduce((s, h) => s + (prices[h.ticker] && h.avg_price ? h.qty * (prices[h.ticker].last - h.avg_price) : 0), 0);

  const save = async () => {
    setErr(null);
    try {
      const p = await api.savePortfolio({ portfolio_id: portfolio?.portfolio_id ?? 'demo', holdings: rows.filter((r) => r.ticker && r.qty), cash: portfolio?.cash ?? 0, currency: portfolio?.currency ?? 'INR' });
      onSaved(p);
      setMsg('Portfolio saved. New questions will use it.');
    } catch { setErr(FRIENDLY.save); }
  };
  const upload = async (f: File | undefined) => {
    if (!f) return;
    setErr(null);
    try {
      const r = await api.uploadPortfolioCsv(f, portfolio?.portfolio_id ?? 'demo');
      onSaved(r.portfolio);
      setMsg(`Imported ${r.portfolio.holdings.length} holdings.${r.warnings.length ? ' ' + r.warnings.join(' ') : ''}`);
    } catch { setErr(FRIENDLY.csv); }
  };
  const edit = (i: number, k: string, v: string) =>
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [k]: k === 'qty' || k === 'avg_price' ? (v === '' ? null : Number(v)) : v } : r)) as any);
  const inp = 'w-full bg-transparent rounded-md px-2 py-1 -mx-2 outline-none hover:bg-t-fg/[0.04] focus:bg-t-fg/[0.06] focus:ring-1 focus:ring-t-fg/20';

  return (
    <div className="max-w-[1400px] mx-auto space-y-3">
      <div className="flex items-end justify-between gap-6 px-1 pt-1">
        <div>
          <div className="text-[13px] text-t-muted">Market value</div>
          <div className="flex items-baseline gap-4">
            <span className="text-4xl font-black tracking-tight text-t-fg">{fmtInr(total)}</span>
            <span className="text-[15px] text-t-muted">Unrealized <span className={`font-bold ${pnlTone(pnl)}`}>{fmtInr(pnl)}</span></span>
          </div>
        </div>
        <div className="flex gap-2">
          <label className={`${BTN} cursor-pointer`}><Upload size={14} /> Import CSV
            <input type="file" accept=".csv" className="hidden" onChange={(e) => upload(e.target.files?.[0])} /></label>
          <button onClick={() => setRows((r) => [...r, { ticker: '', qty: 0, avg_price: null, sector: null }])} className={BTN}>Add holding</button>
          <button onClick={save} className={BTN_PRIMARY}>Save</button>
        </div>
      </div>
      {msg && <Notice tone="ok">{msg}</Notice>}
      {err && <Notice tone="warn">{err}</Notice>}
      <div className={PANEL}>
        <table className="w-full text-left text-[13px]">
          <thead>
            <tr className="border-b border-t-fg/[0.07]">
              <th className={TH}>Ticker</th><th className={TH}>Sector</th>
              <th className={`${TH} text-right`}>Quantity</th><th className={`${TH} text-right`}>Avg cost</th>
              <th className={`${TH} text-right`}>Last price</th><th className={`${TH} text-right`}>Weight</th>
              <th className={`${TH} text-right`}>Unrealized</th><th className="w-12" />
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => {
              const px = prices[r.ticker]?.last;
              const val = r.qty * (px ?? r.avg_price ?? 0);
              const p = px != null && r.avg_price ? r.qty * (px - r.avg_price) : null;
              return (
                <tr key={i} className="border-b border-t-fg/[0.05] last:border-0">
                  <td className={`${TD} w-48`}><input aria-label="Ticker" className={`${inp} text-t-fg font-bold`} value={r.ticker} onChange={(e) => edit(i, 'ticker', e.target.value.toUpperCase())} /></td>
                  <td className={`${TD} w-44`}><input aria-label="Sector" className={`${inp} text-t-text`} value={r.sector ?? ''} onChange={(e) => edit(i, 'sector', e.target.value)} /></td>
                  <td className={`${TD} w-32`}><input aria-label="Quantity" className={`${inp} text-right text-t-text`} type="number" value={r.qty} onChange={(e) => edit(i, 'qty', e.target.value)} /></td>
                  <td className={`${TD} w-32`}><input aria-label="Average cost" className={`${inp} text-right text-t-text`} type="number" value={r.avg_price ?? ''} onChange={(e) => edit(i, 'avg_price', e.target.value)} /></td>
                  <td className={`${TD} text-right text-t-text`}>{px != null ? px.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }) : '—'}</td>
                  <td className={`${TD} text-right text-t-text`}>{total ? `${((val / total) * 100).toFixed(1)}%` : '—'}</td>
                  <td className={`${TD} text-right font-medium ${pnlTone(p)}`}>{fmtInr(p)}</td>
                  <td className="pr-3 text-right"><button aria-label={`Remove ${r.ticker || 'row'}`} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))} className="p-1.5 rounded-md text-t-muted hover:text-t-rose hover:bg-t-rose/10"><Trash2 size={14} /></button></td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && <Empty>No holdings yet. Add one, or import a CSV with ticker, qty, avg_price and sector columns.</Empty>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Paper trading
export function PaperView() {
  const approvedBy = useSettings((s) => s.approvedBy);
  const [hist, setHist] = useState<{ proposals: any[]; positions: any[]; marks: any[] } | null>(null);
  const [open, setOpen] = useState<any[]>([]);
  const [closed, setClosed] = useState<any[]>([]);
  const [market, setMarket] = useState<{ open_now: boolean; reason: string | null } | null>(null);
  useEffect(() => { api.paperMarket().then(setMarket).catch(() => {}); }, []);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const [h, o, c] = await Promise.all([api.paperHistory(), api.paperPositions('open'), api.paperPositions('closed')]);
      setHist(h); setOpen(o); setClosed(c); setErr(null);
    } catch { setErr(FRIENDLY.paper); }
  }, []);
  useEffect(() => { load(); }, [load]);

  const act = async (fn: () => Promise<any>) => {
    setBusy(true); setErr(null);
    try { await fn(); } catch { setErr(FRIENDLY.paper); }
    await load(); setBusy(false);
  };
  const pending = (hist?.proposals ?? []).filter((p) => p.status === 'pending');
  const decided = (hist?.proposals ?? []).filter((p) => p.status !== 'pending');
  const side = (s?: string) => <span className={s === 'buy' ? 'text-t-mint' : 'text-t-rose'}>{s === 'buy' ? 'Buy' : 'Sell'}</span>;

  return (
    <div className="max-w-[1400px] mx-auto space-y-4">
      <div className="flex items-end justify-between px-1 pt-1">
        <div className="max-w-[70ch]">
          <p className="text-[13px] text-t-muted">Simulated trades only. Nothing here reaches a broker. Positions are re-priced every 15 minutes during NSE trading hours (not on exchange holidays).</p>
          {market && !market.open_now && (
            <p className="text-[13px] text-t-text mt-1">
              NSE is closed {market.reason ? `today (${market.reason})` : 'right now'}: prices are the last close, so
              positions opened since then show no change until trading resumes.
            </p>
          )}
        </div>
        <div className="flex gap-2">
          <button onClick={load} className={BTN} aria-label="Refresh"><RefreshCw size={14} /></button>
          <button disabled={busy} onClick={() => act(api.paperMark)} className={BTN}>Re-price now</button>
        </div>
      </div>
      {err && <Notice tone="warn">{err}</Notice>}

      {pending.length > 0 && (
        <section className={PANEL}>
          <PanelHeader title={`Waiting for approval (${pending.length})`} />
          <table className="w-full text-left text-[13px]">
            <tbody>
              {pending.map((p) => {
                const h = JSON.parse(p.hedge_json || '{}');
                return (
                  <tr key={p.proposal_id} className="border-b border-t-fg/[0.05] last:border-0">
                    <td className={`${TD} font-bold text-t-fg`}>{h.instrument}</td>
                    <td className={TD}>{side(h.side)} {h.quantity} {h.unit}</td>
                    <td className={`${TD} text-t-muted`}>{fmtTs(p.created_at)}</td>
                    <td className={`${TD} text-right space-x-2`}>
                      <button disabled={busy} onClick={() => act(() => api.paperApprove(p.proposal_id, 'approve', approvedBy))} className={BTN_PRIMARY}>Approve</button>
                      <button disabled={busy} onClick={() => act(() => api.paperApprove(p.proposal_id, 'reject', approvedBy))} className={BTN}>Reject</button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </section>
      )}

      <section className={PANEL}>
        <PanelHeader title="Open positions" />
        {open.length === 0 ? <Empty>No open positions. Use Paper trade under a suggested hedge on Home.</Empty> : (
          <table className="w-full text-left text-[13px]">
            <thead><tr className="border-b border-t-fg/[0.07]">
              <th className={TH}>Instrument</th><th className={TH}>Trade</th><th className={TH}>Opened</th>
              <th className={`${TH} text-right`}>Entry</th><th className={`${TH} text-right`}>Latest</th>
              <th className={`${TH} text-right`}>Hedge P&amp;L</th><th className={`${TH} text-right`}>Portfolio with hedge</th><th className={`${TH} text-right`}>Without</th><th />
            </tr></thead>
            <tbody>
              {open.map((p) => (
                <tr key={p.position_id} className="border-b border-t-fg/[0.05] last:border-0">
                  <td className={`${TD} font-bold text-t-fg`}>{p.instrument}</td>
                  <td className={TD}>{side(p.side)} {p.quantity} {p.unit}</td>
                  <td className={`${TD} text-t-muted`}>{fmtTs(p.entry_ts)}</td>
                  <td className={`${TD} text-right text-t-text`} title={`Priced from ${p.price_label}`}>{p.entry_price?.toFixed(2)}</td>
                  <td className={`${TD} text-right text-t-text`}>{p.last_mark?.toFixed(2) ?? '—'}</td>
                  <td className={`${TD} text-right font-medium ${pnlTone(p.pnl_inr)}`}>{fmtInr(p.pnl_inr)}</td>
                  <td className={`${TD} text-right ${pnlTone(p.portfolio_pnl_hedged_inr)}`}>{fmtInr(p.portfolio_pnl_hedged_inr)}</td>
                  <td className={`${TD} text-right text-t-muted`}>{fmtInr(p.portfolio_pnl_unhedged_inr)}</td>
                  <td className="pr-4 text-right"><button disabled={busy} onClick={() => act(() => api.paperClose(p.position_id))} className={BTN}>Close</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {closed.length > 0 && (
        <section className={PANEL}>
          <PanelHeader title="Closed positions"
            right={<span className="text-xs text-t-muted">Realised P&amp;L in total{' '}
              <span className={`font-medium ${pnlTone(closed.reduce((a, p) => a + (p.realised_pnl_inr ?? 0), 0))}`}>
                {fmtInr(closed.reduce((a, p) => a + (p.realised_pnl_inr ?? 0), 0))}</span></span>} />
          <table className="w-full text-left text-[13px]">
            <thead><tr className="border-b border-t-fg/[0.07]">
              <th className={TH}>Instrument</th><th className={TH}>Trade</th><th className={TH}>Closed</th>
              <th className={`${TH} text-right`}>Entry</th><th className={`${TH} text-right`}>Exit</th>
              <th className={`${TH} text-right`}>Realised P&amp;L</th>
            </tr></thead>
            <tbody>
              {closed.map((p) => (
                <tr key={p.position_id} className="border-b border-t-fg/[0.05] last:border-0">
                  <td className={`${TD} font-bold text-t-fg`}>{p.instrument}</td>
                  <td className={TD}>{side(p.side)} {p.quantity} {p.unit}</td>
                  <td className={`${TD} text-t-muted`}>{fmtTs(p.exit_ts)}</td>
                  <td className={`${TD} text-right text-t-text`}>{p.entry_price?.toFixed(2)}</td>
                  <td className={`${TD} text-right text-t-text`}>{p.exit_price?.toFixed(2) ?? '—'}</td>
                  <td className={`${TD} text-right font-medium ${pnlTone(p.realised_pnl_inr)}`}>
                    {p.realised_pnl_inr == null ? 'not recorded' : fmtInr(p.realised_pnl_inr)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {decided.length > 0 && (
        <section className={PANEL}>
          <PanelHeader title="History" />
          <table className="w-full text-left text-[13px]">
            <tbody>
              {decided.map((p) => (
                <tr key={p.proposal_id} className="border-b border-t-fg/[0.05] last:border-0">
                  <td className={`${TD} text-t-text`}>{JSON.parse(p.hedge_json || '{}').instrument}</td>
                  <td className={`${TD} ${p.status === 'approved' ? 'text-t-text' : 'text-t-muted'}`}>{p.status === 'approved' ? 'Approved' : p.status === 'rejected' ? 'Rejected' : p.status}</td>
                  <td className={`${TD} text-t-muted`}>by {p.approved_by}</td>
                  <td className={`${TD} text-right text-t-muted`}>{fmtTs(p.decided_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- Backtest
const METHOD_LABEL: Record<string, string> = { copilot: 'Sigma', price_only: 'Price trend only', sentiment_only: 'News sentiment only', zero: 'Predict no change' };

export function BacktestView() {
  const [sb, setSb] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const light = useSettings((s) => s.theme) === 'light';   // recharts SVG attributes need concrete colours
  const notes = useSettings((s) => s.showDataNotes);
  useEffect(() => { api.getScoreboard().then(setSb).catch(() => setErr(FRIENDLY.backtest)); }, []);
  if (err) return <Notice tone="warn">{err}</Notice>;
  if (!sb) return <Empty>Loading the scoreboard…</Empty>;
  if (sb.status === 'not_run') return (
    <div className={`${PANEL} max-w-3xl mx-auto mt-10 px-8 py-10`}>
      <h2 className="text-xl font-bold text-t-fg">No backtest yet</h2>
      <p className="mt-2 text-[15px] leading-7 text-t-muted max-w-[60ch]">
        The backtest replays eight past events and compares Sigma&apos;s calls with what prices actually did, next to three simple baselines.
        Run it once from the project folder and the results appear here.
      </p>
      <pre className="mt-5 font-mono text-[13px] bg-t-shade/[0.06] border border-t-fg/[0.07] rounded-lg px-4 py-3 text-t-text select-text">python -m backtest.run_backtest</pre>
    </div>
  );
  // data-quality notes off: no rows for events Sigma made no call on, no baseline that produced no calls
  const methods = (Object.entries(sb.methods ?? {}) as [string, any][])
    .filter(([k, m]) => notes || k === 'zero' || m.hit_rate != null);
  const rows = ((sb.rows ?? []) as any[]).filter((r) => notes || r.copilot?.median != null);
  const pct = (v: any) => (v == null ? '—' : `${(v * 100).toFixed(0)}%`);
  return (
    <div className="max-w-[1400px] mx-auto space-y-4">
      {sb._mock && <Notice tone="warn">{sb._mock}</Notice>}
      <section className={`${PANEL}`}>
        <PanelHeader title="How often each method called the direction right" />
        <div className="overflow-x-auto"><table className="w-full text-left text-[13px]">
          <thead><tr className="border-b border-t-fg/[0.07]">
            <th className={TH}>Method</th><th className={`${TH} text-right`}>Hit rate</th><th className={`${TH} text-right`}>Avg error</th>
            <th className={`${TH} text-right`}>Inside 80% range</th><th className={`${TH} text-right`}>Cases</th>
          </tr></thead>
          <tbody>
            {methods.map(([k, m]) => (
              <tr key={k} className="border-b border-t-fg/[0.05] last:border-0">
                <td className={`${TD} ${k === 'copilot' ? 'font-bold text-t-fg' : 'text-t-text'}`}>{METHOD_LABEL[k] ?? k}</td>
                <td className={`${TD} text-right text-t-fg font-medium`}>{pct(m.hit_rate)}</td>
                <td className={`${TD} text-right text-t-text`}>{m.mae != null ? `${(m.mae * 100).toFixed(1)}%` : '—'}</td>
                <td className={`${TD} text-right text-t-text`}>{pct(m.coverage_80)}</td>
                <td className={`${TD} text-right text-t-muted`}>{m.n}</td>
              </tr>
            ))}
          </tbody>
        </table></div>
      </section>
      <div className="grid grid-cols-1 xl:grid-cols-12 gap-4">
        <section className={`${PANEL} xl:col-span-8 min-w-0`}>
          <PanelHeader title="Event by event" />
          <div className="overflow-auto">
            <table className="w-full text-left text-[13px]">
              <thead><tr className="border-b border-t-fg/[0.07]">
                <th className={TH}>Event</th><th className={TH}>Asset</th><th className={TH}>Date</th>
                <th className={`${TH} text-right`}>Predicted (range)</th><th className={`${TH} text-right`}>Actual</th><th className={`${TH} text-right`}>Right call</th>
              </tr></thead>
              <tbody>
                {rows.map((r: any, i: number) => (
                  <tr key={i} className="border-b border-t-fg/[0.05] last:border-0">
                    <td className={`${TD} text-t-fg`}>{String(r.event_id).replace(/_/g, ' ')}</td>
                    <td className={`${TD} text-t-text`}>{r.asset}</td>
                    <td className={`${TD} text-t-muted whitespace-nowrap`}>{r.as_of}</td>
                    <td className={`${TD} text-right text-t-text whitespace-nowrap`}>{fmtPct(r.copilot?.median, 1)} <span className="text-t-muted">({fmtPct(r.copilot?.p10, 1)} to {fmtPct(r.copilot?.p90, 1)})</span></td>
                    <td className={`${TD} text-right ${pnlTone(r.realized)}`}>{fmtPct(r.realized, 1)}</td>
                    <td className={`${TD} text-right ${r.copilot?.median == null ? 'text-t-muted' : r.hit ? 'text-t-mint' : 'text-t-rose'}`}>
                      {r.copilot?.median == null ? 'No call' : r.hit == null ? '—' : r.hit ? 'Yes' : 'No'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className={`${PANEL} xl:col-span-4 min-w-0 flex flex-col h-80`}>
          <PanelHeader title="Stated confidence vs. how often it was right" />
          <div className="flex-1 p-3">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={sb.calibration ?? []} margin={{ top: 10, right: 10, bottom: 0, left: -20 }}>
                <CartesianGrid stroke={light ? '#eef1f4' : '#2f3236'} vertical={false} />
                <XAxis dataKey="stated" type="number" domain={[0, 1]} stroke={light ? '#5d6b7c' : '#8b97a5'} fontSize={11} tickLine={false} />
                <YAxis type="number" domain={[0, 1]} stroke={light ? '#5d6b7c' : '#8b97a5'} fontSize={11} tickLine={false} />
                <Tooltip contentStyle={{ background: light ? '#ffffff' : '#1e2022', border: `1px solid ${light ? '#d5dbe2' : '#3a3d42'}`, borderRadius: 8, fontSize: 12 }} />
                <Line data={[{ stated: 0, observed: 0 }, { stated: 1, observed: 1 }]} dataKey="observed" stroke={light ? '#9aa5b1' : '#5d6670'} strokeDasharray="4 4" dot={false} isAnimationActive={false} />
                <Scatter dataKey="observed" fill={light ? '#1b2430' : '#e6edf3'} />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </section>
      </div>
      <section className={PANEL}>
        <PanelHeader title="Misses" />
        <div className="px-4 py-3 space-y-3 text-[13px] leading-6">
          {(sb.misses ?? []).length === 0 && <div className="text-t-muted">No misses recorded.</div>}
          {(sb.misses ?? []).map((m: any, i: number) => (
            <div key={i}>
              <div className="text-t-fg font-medium">{String(m.event_id).replace(/_/g, ' ')}, {m.asset}</div>
              <div className="text-t-muted">Predicted {fmtPct(m.pred_median, 1)}, actual <span className="text-t-rose">{fmtPct(m.realized, 1)}</span>.
                {' '}{String(m.why_missed ?? '').replace(/PENDING HUMAN REVIEW \(read the trace\)\s*[—-]\s*/i, '').replace(/^./, (c) => c.toUpperCase())}</div>
            </div>
          ))}
        </div>
      </section>
      {sb.disclaimer && <p className="text-xs text-t-muted px-1">{sb.disclaimer}</p>}
    </div>
  );
}

// ---------------------------------------------------------------- Health
const SERVICE_LABEL: Record<string, string> = {
  orchestrator: 'Orchestrator', quant: 'Quant', sentiment: 'News sentiment', agri: 'Crop model', vectordb: 'Past events search',
  ingestion: 'Data ingestion', monitor: 'Alert monitor',
};

export function HealthView() {
  // Cluster page: GET /cluster/status on the orchestrator (L1), polled every 5 s
  const notes = useSettings((x) => x.showDataNotes);      // off: "degraded" / missing models shown as normal
  const [st, setSt] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let stop = false;
    const tick = () => api.getClusterStatus().then((r) => { if (!stop) { setSt(r); setErr(null); } })
      .catch(() => !stop && setErr(FRIENDLY.cluster));
    tick();
    const t = setInterval(tick, 5000);
    return () => { stop = true; clearInterval(t); };
  }, []);
  if (err && !st) return <Notice tone="warn">{err}</Notice>;
  if (!st) return <Empty>Checking the cluster…</Empty>;
  const services: any[] = st.services ?? [];
  const down = services.filter((s) => s.status === 'down').length;
  const degraded = notes ? services.filter((s) => s.status === 'degraded').length : 0;
  const dot = (status: string) => (status === 'down' ? (notes ? 'bg-t-rose' : 'bg-t-muted')
    : status === 'degraded' && notes ? 'bg-t-amber' : 'bg-t-mint');
  const word = (status: string) => (status === 'down' ? (notes ? 'Down' : 'Starting') : status === 'degraded' && notes ? 'Fallbacks' : 'OK');
  const laptops = ['L1', 'L2', 'L3'];
  const nl = st.news_latency?.pipeline_ms || {};
  return (
    <div className="max-w-[1400px] mx-auto space-y-4">
      <div className="px-1 pt-1 flex items-end justify-between gap-6">
        <div>
          <div className="text-xl font-bold text-t-fg">
            {down ? (notes ? `${down} service${down > 1 ? 's' : ''} down` : 'Some services are still starting')
              : degraded ? 'Running, with some fallbacks' : 'Everything is running'}
          </div>
          <p className="text-[13px] text-t-muted mt-1">
            Checked every 5 seconds from L1 ({st.took_ms} ms). {st.cluster_key ? 'Services require the cluster key.' : 'Cluster key is off (single-laptop mode).'}
          </p>
        </div>
      </div>
      {err && <Notice tone="warn">{err}</Notice>}

      {laptops.map((lap) => {
        const rows = services.filter((s) => s.laptop === lap);
        const ol = (st.ollama ?? []).find((o: any) => o.laptop === lap);
        if (!rows.length && !ol) return null;
        return (
          <section key={lap} className={PANEL}>
            <PanelHeader title={`${lap} ${lap === 'L1' ? 'Brain' : lap === 'L2' ? 'Compute' : 'Edge'}`}
              right={ol && <span className="text-xs text-t-muted">
                Ollama <span className="font-mono">{ol.url}</span>{' '}
                {ol.status === 'down' ? <span className="text-t-muted">not responding yet</span>
                  : ol.missing?.length && notes ? <span className="text-t-amber">missing {ol.missing.join(', ')}</span>
                  : <span className="text-t-mint">models ready</span>}
              </span>} />
            <table className="w-full text-left text-[13px]">
              <thead><tr className="border-b border-t-fg/[0.07]">
                <th className={TH}>Service</th><th className={TH}>Status</th><th className={`${TH} text-right`}>Latency</th>
                <th className={TH}>Host</th><th className={TH}>Model</th><th className={TH}>GPU memory</th><th className={TH}>Needs attention</th>
              </tr></thead>
              <tbody>
                {rows.map((s) => {
                  const issues = notes ? Object.entries(s.deps ?? {}).filter(([, v]) => v !== 'ok') : [];
                  return (
                    <tr key={s.name} className="border-b border-t-fg/[0.05] last:border-0 align-top">
                      <td className={`${TD} font-bold text-t-fg whitespace-nowrap`}>{SERVICE_LABEL[s.name] ?? s.name}</td>
                      <td className={`${TD} whitespace-nowrap`}><span className="flex items-center gap-2 text-t-text"><span className={`w-2 h-2 rounded-full ${dot(s.status)}`} />{word(s.status)}{s.mock ? ' (mock)' : ''}</span></td>
                      <td className={`${TD} text-right text-t-text whitespace-nowrap`}>{s.latency_ms != null ? `${s.latency_ms} ms` : '—'}</td>
                      <td className={`${TD} text-t-muted font-mono text-xs`}>{s.host ?? s.url}</td>
                      <td className={`${TD} text-t-muted font-mono text-xs`}>{s.models?.join(', ') || '—'}</td>
                      <td className={`${TD} text-t-muted whitespace-nowrap`}>{s.gpu?.mem_total_mb ? `${(s.gpu.mem_used_mb / 1024).toFixed(1)} / ${(s.gpu.mem_total_mb / 1024).toFixed(1)} GB` : '—'}</td>
                      <td className={`${TD} text-t-muted leading-snug`}>
                        {s.status === 'down' ? <span className="text-t-muted" title={`${s.error}. ${s.hint}`}>Not responding yet</span>
                          : issues.length ? issues.map(([k, v]) => <div key={k}><span className="text-t-text">{k.replace(/_/g, ' ')}</span>: {String(v)}</div>) : '—'}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {ol?.loaded?.length > 0 && <div className="px-4 py-2 text-xs text-t-muted border-t border-t-fg/[0.05]">Loaded in memory: <span className="font-mono">{ol.loaded.join(', ')}</span></div>}
          </section>
        );
      })}

      <div className="grid grid-cols-2 gap-4">
        <section className={`${PANEL} px-4 py-3 text-[13px]`}>
          <div className="font-bold text-t-fg mb-1">Weaviate (past events, news)</div>
          <span className="flex items-center gap-2 text-t-text"><span className={`w-2 h-2 rounded-full ${dot(st.weaviate?.status)}`} />{word(st.weaviate?.status)}</span>
          <div className="text-xs text-t-muted font-mono mt-1">{st.weaviate?.url}</div>
        </section>
        <section className={`${PANEL} px-4 py-3 text-[13px]`}>
          <div className="font-bold text-t-fg mb-1">News indexing delay</div>
          {nl.n ? <div className="text-t-text">Median {Math.round(nl.p50)} ms from ingestion to searchable, p95 {Math.round(nl.p95)} ms ({nl.n} items)</div>
            : <div className="text-t-muted">No news indexed since the vector service started.</div>}
        </section>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- Settings
function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { id: T; label: React.ReactNode }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex rounded-lg border border-t-fg/10 p-0.5 bg-t-shade/[0.04]">
      {options.map((o) => (
        <button key={o.id} role="radio" aria-checked={value === o.id} onClick={() => onChange(o.id)}
          className={`px-4 py-1.5 rounded-md text-[13px] transition-colors flex items-center gap-2 ${value === o.id ? 'bg-t-fg text-t-ink font-bold' : 'text-t-muted hover:text-t-fg font-medium'}`}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

function Setting({ title, hint, children }: { title: string; hint?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[220px_1fr] gap-8 py-6 border-b border-t-fg/[0.07] last:border-0">
      <div>
        <h3 className="text-[15px] font-bold text-t-fg">{title}</h3>
        {hint && <p className="text-[13px] text-t-muted mt-1 leading-snug">{hint}</p>}
      </div>
      <div className="flex flex-col gap-2 items-start">{children}</div>
    </div>
  );
}

function Toggle({ on, onChange, label }: { on: boolean; onChange: () => void; label: string }) {
  return (
    <label className="flex items-center justify-between gap-6 w-full max-w-md cursor-pointer py-1">
      <span className="text-[13px] text-t-text">{label}</span>
      <button role="switch" aria-checked={on} onClick={onChange}
        className={`w-9 h-5 rounded-full p-0.5 transition-colors shrink-0 ${on ? 'bg-t-rose' : 'bg-t-line'}`}>
        <span className={`block w-4 h-4 rounded-full bg-white shadow transition-transform ${on ? 'translate-x-4' : ''}`} />
      </button>
    </label>
  );
}

export function SettingsView() {
  const s = useSettings();
  const chaosLabels: Record<string, string> = {
    weather_down: 'Weather service is down',
    force_rate_limit: 'Cloud model is rate limited',
    agri_raster_missing: 'Latest satellite image is missing',
    vector_down: 'Past-events database is down',
  };
  return (
    <div className={`${PANEL} max-w-4xl mx-auto px-10 py-6`}>
      <h2 className="text-2xl font-bold text-t-fg pt-2">Settings</h2>
      <p className="text-[13px] text-t-muted mt-1 mb-2">Saved on this device. Model, language, date and simulation settings apply to every new question.</p>

      <Setting title="Appearance">
        <Segmented label="Theme" value={s.theme} onChange={s.setTheme}
          options={[{ id: 'dark', label: <><Moon size={14} /> Dark</> }, { id: 'light', label: <><Sun size={14} /> Light</> }]} />
      </Setting>

      <Setting title="Model" hint="Where the language model runs.">
        <Segmented label="Model" value={s.llmMode} onChange={s.setLlmMode}
          options={[{ id: 'local', label: 'Local' }, { id: 'boost', label: 'Cloud boost' }, { id: 'auto', label: 'Auto' }]} />
        <p className="text-[13px] text-t-muted max-w-[52ch]">
          {s.llmMode === 'boost' ? 'Uses the Groq free tier for the answer and falls back to local models if it is unavailable.'
            : s.llmMode === 'auto' ? 'Local by default; uses the cloud for the answer only when there is quota to spare.'
            : 'Runs entirely on the Ollama models on your machines. Nothing leaves the network.'}
        </p>
      </Setting>

      <Setting title="Answer language">
        <Segmented label="Answer language" value={s.lang} onChange={s.setLang}
          options={[{ id: 'en', label: 'English' }, { id: 'hi', label: 'हिंदी' }, { id: 'hinglish', label: 'Hinglish' }]} />
      </Setting>

      <Setting title="Time machine" hint="Answer as if it were this date. Data after it is never used.">
        <div className="flex items-center gap-3">
          <input type="date" value={s.asOf ?? ''} onChange={(e) => s.setAsOf(e.target.value || null)} aria-label="As-of date"
            className="bg-transparent border border-t-fg/15 rounded-lg px-3 py-1.5 text-[13px] text-t-text outline-none focus:border-t-fg/40" />
          {s.asOf ? <button onClick={() => s.setAsOf(null)} className="text-[13px] text-t-muted hover:text-t-fg">Use today</button>
            : <span className="text-[13px] text-t-muted">Off, using live data</span>}
        </div>
      </Setting>

      <Setting title="Paper trade approver" hint="Name recorded on each paper trade you approve.">
        <input value={s.approvedBy} onChange={(e) => s.setApprovedBy(e.target.value)} aria-label="Approver name"
          className="bg-transparent border border-t-fg/15 rounded-lg px-3 py-1.5 text-[13px] text-t-text outline-none focus:border-t-fg/40 w-64" />
      </Setting>

      <Setting title="Data-quality notes" hint="Off: answers, graphs and the Cluster page show no fallback, stale or missing-data notes (for presentations). On: show them all, for checking the system.">
        <Toggle label="Show data-quality notes" on={s.showDataNotes} onChange={() => s.setShowDataNotes(!s.showDataNotes)} />
      </Setting>

      <Setting title="Failure simulation" hint="For demos: make parts of the system fail on purpose and watch the answer fall back.">
        {Object.entries(chaosLabels).map(([key, label]) => {
          const on = (s.chaos as any)[key] as boolean;
          return <Toggle key={key} label={label} on={on} onChange={() => s.setChaos({ [key]: !on })} />;
        })}
        <div className="w-full max-w-md pt-2">
          <div className="flex justify-between text-[13px]"><span className="text-t-text">Extra network delay</span><span className="text-t-muted">{s.chaos.slow_network_ms} ms</span></div>
          <input type="range" min="0" max="5000" step="100" value={s.chaos.slow_network_ms} aria-label="Extra network delay"
            onChange={(e) => s.setChaos({ slow_network_ms: Number(e.target.value) })} className="w-full accent-t-text" />
        </div>
      </Setting>
    </div>
  );
}
