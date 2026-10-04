'use client';
// Home: a query thread. The chatbox morphs (framer-motion shared layout) into a turn card holding the reasoning
// and the answer; the newest turn also shows the what-if / heatmap / chart analysis; a new chatbox follows below.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import { ChevronDown, CornerDownRight, Loader2, Plus, Search } from 'lucide-react';
import * as api from '@/lib/api';
import type { AgentEvent, Evidence, FinalAnswer, Intent, Portfolio } from '@/lib/contracts';
import { useSettings } from '@/lib/store';
import { useRunStream } from '@/lib/ws';
import { FRIENDLY, useDemoStream } from '@/lib/demo';
import { cleanEvents, cleanFinal } from '@/lib/present';
import {
  AgentGraph, AnswerBody, ConfidencePill, Empty, EventLog, EvidenceDrawer, IntentPanel, LatencyWaterfall, NodePopover,
  NODE_LABEL, PanelHeader, PriceChart, SectorHeatmap, eventLatency, fmtInr, fmtMs, fmtPct, type Bar,
} from './panels';
import type { PriceMap } from './views';

export interface Turn {
  id: number;
  query: string;
  runId: string | null;
  error: string | null;
  demo?: boolean;          // backend unreachable: show the offline demo (lib/demo.ts), labelled "Demo data"
}

export const EXAMPLES = [
  'A cyclone in the Bay of Bengal and a weak monsoon: what happens to my FMCG and agri stocks?',
  'A category 4 hurricane is heading for Louisiana. How does it affect my portfolio?',
  'If the RBI raises the repo rate by 25 bps, how exposed are my bank holdings?',
  'Crude is up 10% this week. Which holdings get hurt, and how should I hedge?',
];

const SPRING = { type: 'spring' as const, stiffness: 170, damping: 26 };
// inner panels sit on the cream card
const INNER = 'bg-t-panel border border-black/[0.06] rounded-xl overflow-hidden';
const INK = 'text-[#121821]';

// ---------------------------------------------------------------- chatbox
function ChatBox({ layoutId, big, busy, onSubmit, placeholder, autoFocus }: {
  layoutId: string; big?: boolean; busy: boolean; onSubmit: (q: string) => void; placeholder: string; autoFocus?: boolean;
}) {
  const [value, setValue] = useState('');
  const send = (q = value) => { if (q.trim() && !busy) { onSubmit(q.trim()); setValue(''); } };
  return (
    <motion.div initial={big ? false : { opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.6, duration: 0.35 }}>
      <motion.div layoutId={layoutId} transition={SPRING}
        className="w-full bg-[#f5e6d3] border border-[#e3d1c1] rounded-2xl p-2 shadow-xl shadow-black/15 flex items-center transition-[border-color] focus-within:border-[#121821]/30 group">
        <div className={`${INK} ml-4 mr-3 opacity-40 group-focus-within:opacity-80 transition-opacity`}>
          {big ? <Search size={22} /> : <CornerDownRight size={20} />}
        </div>
        <input type="text" value={value} onChange={(e) => setValue(e.target.value)} autoFocus={autoFocus}
          onKeyDown={(e) => { if (e.key === 'Enter') send(); }} placeholder={placeholder} aria-label="Your question"
          className={`flex-1 min-w-0 bg-transparent border-none outline-none ${INK} placeholder-[#121821]/40 ${big ? 'text-xl py-4' : 'text-base py-3'}`} />
        <button onClick={() => send()} disabled={busy || !value.trim()}
          className={`bg-[#2c2d2d] hover:bg-black text-[#f5e6d3] rounded-xl font-bold transition-colors mx-1 flex items-center gap-2 disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#121821] ${big ? 'px-7 py-3.5 text-[15px]' : 'px-5 py-2.5 text-sm'}`}>
          {busy && <Loader2 size={15} className="animate-spin" />} Ask
        </button>
      </motion.div>
      <RunSettingsLine />
      {big && (
        <div className="mt-10 grid grid-cols-2 gap-2.5">
          {EXAMPLES.map((p) => (
            <button key={p} onClick={() => send(p)}
              className="text-left text-sm leading-snug text-t-text bg-t-panel/80 backdrop-blur-sm border border-t-fg/10 hover:border-t-fg/25 rounded-xl px-4 py-3 transition-colors">
              {p}
            </button>
          ))}
        </div>
      )}
    </motion.div>
  );
}

/** One quiet line saying how the question will be run; warnings only when something non-default is on. */
function RunSettingsLine() {
  const s = useSettings();
  const chaos = Object.entries(s.chaos).some(([k, v]) => k !== 'slow_network_ms' && v);
  const model = { local: 'Local models', boost: 'Cloud boost', auto: 'Auto model choice' }[s.llmMode];
  const lang = { en: 'English', hi: 'Hindi', hinglish: 'Hinglish' }[s.lang];
  return (
    <div className="flex gap-4 mt-2.5 justify-end text-xs text-t-muted">
      <div className="flex gap-4 bg-t-panel/80 backdrop-blur-sm rounded-md px-2.5 py-1">
      <span>{model}, answers in {lang}</span>
      {s.asOf && <span className="text-t-amber">Time machine: {s.asOf}</span>}
      {chaos && <span className="text-t-rose">Failure simulation on</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- one turn
function useElapsed(running: boolean) {
  const [t0] = useState(() => Date.now());
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    const i = setInterval(() => setNow(Date.now()), 100);
    return () => clearInterval(i);
  }, [running]);
  return ((now - t0) / 1000).toFixed(1);
}

function TurnCard({ turn, isLatest, layoutId, portfolio, bars, prices, onAsk }: {
  turn: Turn; isLatest: boolean; layoutId: string; portfolio: Portfolio | null; bars: Record<string, Bar[]>; prices: PriceMap;
  onAsk: (q: string) => void;
}) {
  const live = useRunStream(turn.demo ? null : turn.runId);
  // pitch safety net: if the backend drops the run (connection error) or never answers, replay the offline demo
  const [fallback, setFallback] = useState(false);
  useEffect(() => {
    if (!turn.demo && live.status === 'error' && !live.final) setFallback(true);
  }, [turn.demo, live.status, live.final]);
  useEffect(() => {
    if (turn.demo || !turn.runId || live.final) return;
    const t = setTimeout(() => setFallback(true), 150_000);
    return () => clearTimeout(t);
  }, [turn.demo, turn.runId, live.final]);
  const isDemo = !!turn.demo || !!turn.error || fallback;
  const demo = useDemoStream(isDemo ? turn.query : null);
  const raw = isDemo ? demo : live;
  const status = raw.status;
  // Settings > "Show data-quality notes" off (default): no fallback / missing-data marks anywhere (lib/present.ts)
  const showNotes = useSettings((s) => s.showDataNotes);
  const events = useMemo(() => (showNotes ? raw.events : cleanEvents(raw.events)), [raw.events, showNotes]);
  const final = useMemo(() => (showNotes ? raw.final : cleanFinal(raw.final)), [raw.final, showNotes]);
  const [cite, setCite] = useState<string | null>(null);
  const [settled, setSettled] = useState(false);
  const running = !final && status !== 'error';
  const elapsed = useElapsed(running);
  const evidenceById = useMemo(() => Object.fromEntries((final?.evidence ?? []).map((e) => [e.id, e])) as Record<string, Evidence>, [final]);
  useEffect(() => { const t = setTimeout(() => setSettled(true), 900); return () => clearTimeout(t); }, []);

  // greetings, help, unclear ... come back as a short reply with suggestions (no agents ran)
  const conversation = final?.kind === 'conversation' || events.some((e) => e.meta?.fast_path);
  const waiting = !final && events.length === 0;
  const stateChip = isDemo && final
    ? <span className="text-xs font-medium bg-t-fg/[0.08] text-t-muted px-2 py-1 rounded-md" title="The live services did not answer, so this shows the offline demo">Demo data</span>
    : conversation ? null
    : final ? <ConfidencePill c={final.confidence} />
    : <span className="text-xs font-medium bg-[#2c2d2d] text-[#f5e6d3] px-2.5 py-1 rounded-md flex items-center gap-1.5 whitespace-nowrap"><Loader2 size={11} className="animate-spin" /> Thinking, {elapsed} s</span>;

  return (
    <motion.div layoutId={settled ? undefined : layoutId} transition={SPRING}
      className="w-full bg-[#f5e6d3] border border-[#e3d1c1] rounded-2xl p-2.5 shadow-xl shadow-black/15">
      <div className={`flex items-start gap-3 px-3 pt-2 pb-3 ${INK}`}>
        <Search size={18} className="opacity-40 shrink-0 mt-1" />
        <h2 className="flex-1 text-lg font-bold leading-snug">{turn.query}</h2>
        <div className="shrink-0 mt-0.5">{stateChip}</div>
      </div>
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.35, duration: 0.4 }} className="space-y-2.5">
        {waiting ? (
          <div className={`${INNER} px-5 py-4 flex gap-1.5`} aria-label="Waiting for a reply">
            {[0, 1, 2].map((i) => <span key={i} className="w-1.5 h-1.5 rounded-full bg-t-muted animate-pulse" style={{ animationDelay: `${i * 150}ms` }} />)}
          </div>
        ) : conversation ? (
          final && <ConversationReply final={final} onAsk={onAsk} />
        ) : (<>
          {/* answer first; how it was reached (graph, timings, log) and the analysis panels below it */}
          <AnswerBox final={final} events={events} running={running} onCite={setCite} />
          <ReasoningBox events={events} final={final} status={status} running={running} elapsed={elapsed}
            defaultOpen={isLatest} runId={turn.runId} onCite={setCite} />
          {isLatest && final && <AnalysisBox final={final} portfolio={portfolio} bars={bars} prices={prices} />}
        </>)}
      </motion.div>
      {cite && <EvidenceDrawer id={cite} evidence={evidenceById[cite]} onClose={() => setCite(null)} />}
    </motion.div>
  );
}

function ConversationReply({ final, onAsk }: { final: FinalAnswer; onAsk: (q: string) => void }) {
  return (
    <div className={`${INNER} px-6 py-5`}>
      <p className="text-[15px] leading-7 text-t-text max-w-[68ch]">{final.answer_markdown}</p>
      {(final.suggestions ?? []).length > 0 && (
        <div className="mt-4 flex flex-col items-start gap-2">
          {final.suggestions!.map((q) => (
            <button key={q} onClick={() => onAsk(q)}
              className="text-left text-sm text-t-fg border border-t-fg/15 hover:border-t-fg/35 hover:bg-t-fg/[0.04] rounded-lg px-3.5 py-2 transition-colors">
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function ReasoningBox({ events, final, status, running, elapsed, defaultOpen, runId, onCite }: {
  events: AgentEvent[]; final: FinalAnswer | null; status: string; running: boolean; elapsed: string; defaultOpen: boolean;
  runId: string | null; onCite: (id: string) => void;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => { if (!defaultOpen) setOpen(false); }, [defaultOpen]);
  const [selNode, setSelNode] = useState<string | null>(null);
  const intent: Intent | null = final?.intent ?? (events.find((e) => e.node === 'parse_intent' && e.meta?.intent)?.meta.intent ?? null);
  const latency = final?.latency_ms && Object.keys(final.latency_ms).length ? final.latency_ms : eventLatency(events);
  const degraded = new Set(events.filter((e) => e.status === 'degraded').map((e) => e.node)).size;
  const total = final?.latency_ms?.total;
  const last = events[events.length - 1];

  return (
    <div className={INNER}>
      <button onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="w-full px-4 h-12 flex items-center gap-4 text-left hover:bg-t-fg/[0.02]">
        <span className="text-[15px] font-bold text-t-fg">Reasoning</span>
        <span className="text-[13px] text-t-muted whitespace-nowrap shrink-0">
          {events.length} steps, {total ? fmtMs(total) : `${elapsed} s`}
          {degraded > 0 && <span className="text-t-saffron">, {degraded} used fallback data</span>}
        </span>
        <span className="ml-auto flex items-center gap-4 min-w-0">
          {running && last
            ? <span className="text-[13px] text-t-text truncate">{NODE_LABEL[last.node] ?? last.node}{last.message ? `: ${last.message}` : ''}</span>
            : runId && <span className="text-[11px] font-mono text-t-muted">{runId}</span>}
          <ChevronDown size={16} className={`text-t-muted shrink-0 transition-transform ${open ? 'rotate-180' : ''}`} />
        </span>
      </button>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div initial={{ height: 0, opacity: 0 }} animate={{ height: 'auto', opacity: 1 }} exit={{ height: 0, opacity: 0 }} transition={{ duration: 0.3 }}>
            <div className="grid grid-cols-12 gap-3 px-3 pb-3 border-t border-t-fg/[0.07] pt-3">
              <div className="col-span-12 h-[280px] relative rounded-lg bg-t-panel2 border border-t-fg/[0.07] overflow-hidden">
                <AgentGraph events={events} onSelect={setSelNode} />
                {selNode && <NodePopover node={selNode} events={events} onClose={() => setSelNode(null)} onCite={onCite} />}
                {!selNode && events.length > 0 && <span className="absolute left-3 bottom-2 text-[11px] text-t-muted">Select a step to see what it did</span>}
              </div>
              <div className="col-span-5 h-56 rounded-lg border border-t-fg/[0.07] flex flex-col overflow-hidden">
                <PanelHeader title="How the question was read" />
                <IntentPanel intent={intent} events={events} />
              </div>
              <div className="col-span-7 h-56 rounded-lg border border-t-fg/[0.07] flex flex-col overflow-hidden">
                <PanelHeader title="Time per step" />
                <LatencyWaterfall latency={latency} events={events} />
              </div>
              <div className="col-span-12 h-36 rounded-lg border border-t-fg/[0.07] flex flex-col overflow-hidden">
                <PanelHeader title="Log" right={<span className="text-xs text-t-muted">{status}</span>} />
                {events.length ? <EventLog events={events} /> : <Empty>Waiting for the orchestrator to start.</Empty>}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

const VERDICT: Record<string, { text: string; tone: string }> = {
  'proceed': { text: 'Fine to proceed', tone: 'text-t-mint' },
  'proceed with caution': { text: 'Proceed with caution', tone: 'text-t-amber' },
  'do not act': { text: 'Do not act on this yet', tone: 'text-t-rose' },
};

function AnswerBox({ final, events, running, onCite }: { final: FinalAnswer | null; events: AgentEvent[]; running: boolean; onCite: (id: string) => void }) {
  if (!final) {
    return (
      <div className={`${INNER} px-6 py-5`}>
        <div className="text-[15px] font-bold text-t-fg mb-4">Answer</div>
        {running ? (
          <div className="space-y-3 max-w-[68ch]">
            <div className="space-y-2.5 animate-pulse">
              {[92, 78, 85, 58].map((w, i) => <div key={i} className="h-3 bg-t-fg/[0.08] rounded" style={{ width: `${w}%` }} />)}
            </div>
            <div className="text-[13px] text-t-muted pt-1">{events.some((e) => e.node === 'synthesizer' && e.status === 'started') ? 'Writing the answer from the evidence.' : 'Collecting evidence from the agents.'}</div>
          </div>
        ) : <div className="text-t-muted text-sm">{FRIENDLY.generic}</div>}
      </div>
    );
  }
  const v = final.red_team ? VERDICT[final.red_team.verdict] ?? { text: final.red_team.verdict, tone: 'text-t-text' } : null;
  const val = final.validator;
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.4 }} className={`${INNER} grid grid-cols-12`}>
      <div className="col-span-8 flex flex-col min-w-0">
        <div className="px-6 pt-5 text-[15px] font-bold text-t-fg">Answer</div>
        <AnswerBody final={final} onCite={onCite} />
      </div>
      <aside className="col-span-4 flex flex-col border-l border-t-fg/[0.07] text-[13px]">
        <section className="px-5 py-4 border-b border-t-fg/[0.07]">
          <h3 className="text-xs text-t-muted mb-1">Second opinion</h3>
          {final.red_team && v ? (<>
            <div className={`text-[15px] font-bold ${v.tone}`} title={final.red_team.verdict_reason}>{v.text}</div>
            <ul className="mt-2 space-y-1.5 text-t-text leading-snug">
              {final.red_team.reasons.slice(0, 3).map((r, i) => <li key={i} className="pl-3 relative"><span className="absolute left-0 top-[0.55em] w-1 h-1 rounded-full bg-t-muted" />{r.replace(/\s*\[ev_[a-z0-9_]+\]/g, '')}</li>)}
            </ul>
          </>) : <div className="text-t-muted">Not run for this question.</div>}
        </section>
        <section className="px-5 py-4 border-b border-t-fg/[0.07]">
          <h3 className="text-xs text-t-muted mb-1">Number check</h3>
          {val.numbers_found === 0
            ? <div className="text-t-text">No figures to check.</div>
            : <div className={val.action === 'pass' ? 'text-t-text' : 'text-t-amber'}>
                <span className="font-bold">{val.numbers_matched} of {val.numbers_found}</span> figures match the evidence
              </div>}
          {val.unmatched.length > 0 && <div className="text-t-muted mt-1" title={val.unmatched.join(', ')}>Not found: {val.unmatched.slice(0, 3).join(', ')}{val.unmatched.length > 3 ? '…' : ''}</div>}
        </section>
        <HedgePanel final={final} />
      </aside>
    </motion.div>
  );
}

function HedgePanel({ final }: { final: FinalAnswer }) {
  const approvedBy = useSettings((s) => s.approvedBy);
  const [state, setState] = useState<Record<string, string>>({});
  const approve = async (hedgeId: string) => {
    setState((s) => ({ ...s, [hedgeId]: 'Opening paper trade…' }));
    try {
      const prop = await api.paperPropose(final.run_id, hedgeId);
      const r = await api.paperApprove(prop.proposal_id, 'approve', approvedBy);
      setState((s) => ({ ...s, [hedgeId]: r.position ? `Paper trade opened at ${Number(r.position.entry_price).toFixed(2)}` : `Proposal ${r.proposal?.status ?? 'saved'}` }));
    } catch (e: any) {
      setState((s) => ({ ...s, [hedgeId]: FRIENDLY.hedge }));
    }
  };
  return (
    <section className="px-5 py-4 flex-1">
      <h3 className="text-xs text-t-muted mb-2">Suggested hedges</h3>
      {!final.hedges.length ? <div className="text-t-text">No hedge suggested for this question.</div> : (
        <ul className="space-y-3">
          {final.hedges.map((h) => {
            const st = state[h.hedge_id];
            return (
              <li key={h.hedge_id} title={h.rationale}>
                <div className="font-bold text-t-fg">{h.instrument}</div>
                <div className="text-t-muted leading-snug">
                  <span className={h.side === 'buy' ? 'text-t-mint' : 'text-t-rose'}>{h.side === 'buy' ? 'Buy' : 'Sell'}</span> {h.quantity} {h.unit}, hedge ratio {h.hedge_ratio.toFixed(2)}
                  {h.est_cost_inr != null && <>, about {fmtInr(h.est_cost_inr)}</>}
                </div>
                {st ? <div className="mt-1.5 text-t-text">{st}</div> : (
                  <button onClick={() => approve(h.hedge_id)}
                    className="mt-2 text-[13px] font-medium text-t-fg border border-t-fg/20 hover:bg-t-fg/[0.06] rounded-md px-3 py-1.5 transition-colors">
                    Paper trade
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------- analysis (latest turn only)
function AnalysisBox({ final, portfolio, bars }: { final: FinalAnswer | null; portfolio: Portfolio | null; bars: Record<string, Bar[]>; prices: PriceMap }) {
  const [exposure, setExposure] = useState<any>(null);
  useEffect(() => {
    const ev = final?.evidence.find((e) => e.tool === 'exposure' && e.value?.heatmap);
    if (ev) { setExposure(ev.value); return; }
    if (portfolio && !exposure) api.postExposure(portfolio).then((r) => setExposure(r.evidence[0]?.value ?? null)).catch(() => {});
  }, [final, portfolio]); // eslint-disable-line react-hooks/exhaustive-deps

  const chartTickers = Object.keys(bars);
  const [chartTicker, setChartTicker] = useState<string | null>(null);
  useEffect(() => {
    const fromIntent = final?.intent.tickers.find((t) => bars[t]);
    if (fromIntent) setChartTicker(fromIntent);
    else if (!chartTicker && chartTickers.length) setChartTicker(chartTickers[0]);
  }, [final, chartTickers.length]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.5, duration: 0.4 }} className="grid grid-cols-12 gap-2.5">
      <div className={`${INNER} col-span-4 h-[380px] flex flex-col`}><WhatIf portfolio={portfolio} /></div>
      <div className={`${INNER} col-span-4 h-[380px] flex flex-col`}>
        <PanelHeader title="Sector sensitivity" />
        <SectorHeatmap value={exposure} />
      </div>
      <div className={`${INNER} col-span-4 h-[380px] flex flex-col`}>
        <PanelHeader title="Price, last 3 months" right={chartTickers.length > 0 ? (
          <select value={chartTicker ?? ''} onChange={(e) => setChartTicker(e.target.value)} aria-label="Ticker"
            className="bg-t-line/70 text-xs text-t-text rounded-md px-2 py-1 outline-none focus-visible:ring-2 focus-visible:ring-t-fg/30">
            {chartTickers.map((t) => <option key={t}>{t}</option>)}
          </select>) : null} />
        <div className="flex-1 w-full relative" style={{ minHeight: 0 }}>
          <PriceChart rows={chartTicker ? bars[chartTicker] ?? [] : []} />
        </div>
      </div>
    </motion.div>
  );
}

const SLIDERS: { key: string; label: string; min: number; max: number; unit: string }[] = [
  { key: 'monsoon_rain', label: 'Monsoon rain', min: -40, max: 20, unit: '%' },
  { key: 'crude', label: 'Crude oil', min: -30, max: 30, unit: '%' },
  { key: 'nifty', label: 'Nifty', min: -15, max: 15, unit: '%' },
  { key: 'usd_inr', label: 'USD/INR', min: -5, max: 5, unit: '%' },
  { key: 'repo_bps', label: 'Repo rate', min: -50, max: 50, unit: ' bps' },
];

function WhatIf({ portfolio }: { portfolio: Portfolio | null }) {
  const [shocks, setShocks] = useState<Record<string, number>>({ monsoon_rain: -20, crude: 5, nifty: 0, usd_inr: 0, repo_bps: 0 });
  const [res, setRes] = useState<Evidence | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const showNotes = useSettings((s) => s.showDataNotes);
  useEffect(() => {
    if (!portfolio) return;
    const t = setTimeout(() => {
      setBusy(true);
      const nonZero = Object.fromEntries(Object.entries(shocks).filter(([, v]) => v !== 0));
      api.postScenario({ portfolio, shocks: nonZero })
        .then((r) => { setRes(r.evidence[0] ?? null); setErr(null); })
        .catch(() => setErr(FRIENDLY.scenario))
        .finally(() => setBusy(false));
    }, 300);
    return () => clearTimeout(t);
  }, [shocks, portfolio]);
  const v = res?.value;
  return (<>
    <PanelHeader title="What if" right={busy ? <Loader2 size={12} className="animate-spin text-t-muted" /> : null} />
    <div className="flex-1 px-4 pt-3 pb-4 flex flex-col gap-2 overflow-auto">
      {SLIDERS.map((s) => (
        <label key={s.key} className="block">
          <div className="flex justify-between text-[13px]"><span className="text-t-muted">{s.label}</span>
            <span className="text-t-text">{shocks[s.key] > 0 ? '+' : ''}{shocks[s.key]}{s.unit}</span></div>
          <input type="range" className="w-full accent-t-text h-4" min={s.min} max={s.max} value={shocks[s.key]}
            onChange={(e) => setShocks((x) => ({ ...x, [s.key]: Number(e.target.value) }))} />
        </label>
      ))}
      <div className="mt-auto pt-3 border-t border-t-fg/[0.07]">
        <div className="text-xs text-t-muted">Portfolio impact</div>
        {err ? <div className="text-[13px] text-t-muted">{err}</div> : (
          <div className="flex items-baseline gap-2">
            <span className={`text-2xl font-bold ${(v?.pnl_inr ?? 0) < 0 ? 'text-t-rose' : 'text-t-mint'}`}>{fmtInr(v?.pnl_inr)}</span>
            <span className="text-[13px] text-t-muted">{fmtPct(v?.pnl_pct)}</span>
            {res?.degraded && showNotes && <span className="text-xs text-t-saffron">estimated with fallback data</span>}
          </div>
        )}
      </div>
    </div>
  </>);
}

// ---------------------------------------------------------------- the home view
export function HomeView({ turns, nextId, onSubmit, onNewChat, submitting, portfolio, bars, prices }: {
  turns: Turn[]; nextId: number; onSubmit: (q: string) => void; onNewChat: () => void; submitting: boolean;
  portfolio: Portfolio | null; bars: Record<string, Bar[]>; prices: PriceMap;
}) {
  const scroller = useRef<HTMLDivElement>(null);
  const lastTurn = useRef<HTMLDivElement>(null);
  // scroll to the new turn by offsetTop (layout position): getBoundingClientRect would include the morph transform
  useEffect(() => {
    if (turns.length < 2) { scroller.current?.scrollTo({ top: 0 }); return; }
    const t = setTimeout(() => {
      if (scroller.current && lastTurn.current) scroller.current.scrollTo({ top: lastTurn.current.offsetTop - 16, behavior: 'smooth' });
    }, 150);
    return () => clearTimeout(t);
  }, [turns.length]);
  const empty = turns.length === 0;

  return (
    <div ref={scroller} className="flex-1 overflow-auto custom-scrollbar relative" id="home-scroller">
      <div className={`w-full max-w-6xl mx-auto px-6 ${empty ? 'min-h-full flex flex-col justify-center pb-24' : 'py-6 space-y-6'}`}>
        <AnimatePresence>
          {empty && (
            <motion.div key="hero" initial={{ opacity: 0, y: 20 }} animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -40, height: 0, marginBottom: 0 }} transition={{ duration: 0.35 }}
              className="relative w-full max-w-3xl mx-auto mb-10">
              {/* soft clearing so the terrain does not run under the text */}
              <div aria-hidden className="absolute -inset-x-10 -inset-y-8 -z-10 rounded-[40px] bg-t-ink/75 blur-2xl" />
              <h1 className="text-[52px] leading-[1.05] font-black tracking-tight text-t-fg">What&apos;s on your mind?</h1>
              <p className="mt-4 text-[17px] leading-relaxed text-t-text/85 max-w-[56ch]">
                Ask how an event could move your portfolio. Agents pull weather, news, crop and market data,
                and every figure in the answer is checked against its source.
              </p>
            </motion.div>
          )}
        </AnimatePresence>

        {!empty && (
          <div className="flex justify-end">
            <button onClick={onNewChat} className="text-[13px] text-t-text flex items-center gap-1.5 bg-t-panel/80 border border-t-fg/10 hover:border-t-fg/25 rounded-lg px-3 py-1.5 transition-colors">
              <Plus size={14} /> New chat
            </button>
          </div>
        )}

        {turns.map((t, i) => (
          <div key={t.id} ref={i === turns.length - 1 ? lastTurn : undefined} className="scroll-mt-4">
            <TurnCard turn={t} isLatest={i === turns.length - 1} layoutId={`box-${t.id}`} portfolio={portfolio} bars={bars}
              prices={prices} onAsk={onSubmit} />
          </div>
        ))}

        <div className={empty ? 'w-full max-w-3xl mx-auto' : 'w-full max-w-4xl mx-auto pb-10'}>
          <ChatBox key={nextId} layoutId={`box-${nextId}`} big={empty} busy={submitting}
            onSubmit={onSubmit} autoFocus={empty}
            placeholder={empty ? 'Ask about an event, a stock or your portfolio' : 'Ask a follow-up question'} />
        </div>
      </div>
    </div>
  );
}
