'use client';
// Live panels of the Overview tab, all fed by the orchestrator run stream / backend tools.
import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ReactFlow, Background, Position } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { createChart } from 'lightweight-charts';
import { X } from 'lucide-react';
import type { AgentEvent, Evidence, FinalAnswer, Intent } from '@/lib/contracts';
import { splitCitations } from '@/lib/citations';
import { useSettings } from '@/lib/store';

export const PANEL = 'bg-t-panel/95 backdrop-blur-md border border-t-fg/10 rounded-xl overflow-hidden';
export function PanelHeader({ title, right }: { title: string; right?: React.ReactNode }) {
  return (
    <div className="px-4 h-11 border-b border-t-fg/[0.07] flex items-center justify-between gap-3 shrink-0">
      <h3 className="text-[13px] font-bold text-t-text truncate">{title}</h3>
      {right}
    </div>
  );
}
export function Empty({ children }: { children: React.ReactNode }) {
  return <div className="flex-1 flex items-center justify-center text-[13px] text-t-muted px-6 py-8 text-center leading-relaxed">{children}</div>;
}

export const fmtInr = (v: number | null | undefined, digits = 0) =>
  v == null || Number.isNaN(v) ? '—' : `${v < 0 ? '-' : ''}₹${Math.abs(v).toLocaleString('en-IN', { maximumFractionDigits: digits })}`;
export const fmtPct = (v: number | null | undefined, digits = 2) =>
  v == null || Number.isNaN(v) ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`;

// ---------------------------------------------------------------- agent graph
// [node id from the orchestrator, label shown to the user, x, y]; flows left to right
const NODE_LAYOUT: [string, string, number, number][] = [
  ['parse_intent', 'Read question', 0, 110],
  ['router', 'Pick agents', 165, 110],
  ['explain', 'Explain', 165, 230],
  ['sentiment_agent', 'News sentiment', 340, 20],
  ['weather_agent', 'Weather', 340, 110],
  ['agri_agent', 'Crops', 340, 200],
  ['macro_agent', 'Macro', 500, 20],
  ['analog_agent', 'Past events', 500, 110],
  ['exposure_agent', 'Exposure', 500, 200],
  ['join', 'Combine', 670, 110],
  ['planner', 'Fill gaps', 670, 210],
  ['quant_agent', 'Risk and hedges', 835, 110],
  ['synthesizer', 'Write answer', 1010, 110],
  ['red_team', 'Challenge it', 1175, 110],
  ['validator', 'Check numbers', 1340, 110],
];
const AGENTS = NODE_LAYOUT.slice(3, 9).map((n) => n[0]);
export const NODE_LABEL: Record<string, string> = Object.fromEntries(NODE_LAYOUT.map(([id, label]) => [id, label]));
const STATUS_WORD: Record<string, string> = { started: 'running', progress: 'running', skipped: 'skipped', failed: 'failed', queued: 'queued' };
export const fmtMs = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const EDGES: [string, string][] = [
  ['parse_intent', 'router'], ['router', 'explain'],
  ...AGENTS.map((a) => ['router', a] as [string, string]),
  ...AGENTS.map((a) => [a, 'join'] as [string, string]),
  ['join', 'planner'], ['planner', 'quant_agent'], ['join', 'quant_agent'],
  ['quant_agent', 'synthesizer'], ['synthesizer', 'red_team'], ['red_team', 'validator'],
];

const STATUS_STYLE: Record<string, string> = {
  idle: 'border-t-fg/10 text-t-muted',
  queued: 'border-t-fg/10 text-t-muted',
  started: 'border-t-amber animate-pulse',
  progress: 'border-t-amber animate-pulse',
  finished: 'border-t-mint',
  degraded: 'border-t-saffron bg-[repeating-linear-gradient(45deg,rgb(var(--c-panel)),rgb(var(--c-panel))_4px,rgb(var(--c-saffron)/0.18)_4px,rgb(var(--c-saffron)/0.18)_8px)]',
  failed: 'border-t-rose text-t-rose',
  skipped: 'border-t-fg/10 border-dashed text-t-muted opacity-60',
};
// CSS variables, so edges follow the theme (globals.css)
const V = (c: string) => `rgb(var(--c-${c}))`;
const EDGE_COLOR: Record<string, string> = { finished: V('mint'), degraded: V('saffron'), failed: V('rose'), started: V('amber'), progress: V('amber') };

export function latestByNode(events: AgentEvent[]) {
  const m: Record<string, AgentEvent> = {};
  for (const e of events) m[e.node] = e;
  return m;
}

export function AgentGraph({ events, onSelect }: { events: AgentEvent[]; onSelect: (node: string) => void }) {
  const latest = useMemo(() => latestByNode(events), [events]);
  const theme = useSettings((s) => s.theme);
  // refit whenever the box resizes: the first fitView can run mid-animation (collapsed / morphing card)
  const wrap = useRef<HTMLDivElement>(null);
  const flow = useRef<{ fitView: () => unknown } | null>(null);
  useEffect(() => {
    if (!wrap.current) return;
    let t: ReturnType<typeof setTimeout>;
    const ro = new ResizeObserver(() => { clearTimeout(t); t = setTimeout(() => flow.current?.fitView(), 60); });
    ro.observe(wrap.current);
    return () => { clearTimeout(t); ro.disconnect(); };
  }, []);
  const nodes = NODE_LAYOUT.map(([id, label, x, y]) => {
    const ev = latest[id];
    const st = ev?.status ?? 'idle';
    const sub = !ev ? '' : ev.latency_ms != null && !STATUS_WORD[st] ? fmtMs(ev.latency_ms) : STATUS_WORD[st] ?? st;
    return {
      id,
      position: { x, y },
      sourcePosition: Position.Right,
      targetPosition: Position.Left,
      data: {
        label: (
          <div title={ev?.message ?? ''}>
            <div className="font-medium">{label}</div>
            {sub && <div className="text-[11px] text-t-muted mt-0.5">{sub}</div>}
          </div>
        ),
      },
      className: `bg-t-panel text-t-text text-[13px] border px-3 py-2 rounded-md !w-[140px] ${STATUS_STYLE[st] ?? STATUS_STYLE.idle}`,
    };
  });
  const edges = EDGES.map(([s, t]) => {
    const tgt = latest[t]?.status;
    const src = latest[s]?.status;
    const active = tgt === 'started' || tgt === 'progress';
    const color = active ? V('amber') : (tgt && EDGE_COLOR[tgt]) || (src && EDGE_COLOR[src] && tgt ? EDGE_COLOR[src] : V('edge'));
    return { id: `${s}-${t}`, source: s, target: t, animated: active, style: { stroke: color } };
  });
  return (
    <div ref={wrap} className="w-full h-full">
      <ReactFlow nodes={nodes} edges={edges} fitView proOptions={{ hideAttribution: true }} colorMode={theme}
        nodesDraggable={false} onNodeClick={(_, n) => onSelect(n.id)} onInit={(rf) => { flow.current = rf; }}>
        <Background color={theme === 'light' ? 'rgba(15,23,32,0.05)' : 'rgba(255,255,255,0.02)'} gap={16} size={1} />
      </ReactFlow>
    </div>
  );
}

export function NodePopover({ node, events, onClose, onCite }: { node: string; events: AgentEvent[]; onClose: () => void; onCite: (id: string) => void }) {
  const evs = events.filter((e) => e.node === node);
  return (
    <div className="absolute inset-x-3 bottom-3 z-20 bg-t-panel2 border border-t-fg/10 rounded-lg shadow-lg p-4 max-h-[65%] overflow-auto text-[13px]">
      <div className="flex justify-between items-center mb-3">
        <span className="text-t-fg font-bold">{NODE_LABEL[node] ?? node}</span>
        <button onClick={onClose} aria-label="Close" className="text-t-muted hover:text-t-fg"><X size={14} /></button>
      </div>
      {evs.length === 0 && <div className="text-t-muted">This step did not run.</div>}
      <ol className="space-y-2">
        {evs.map((e) => (
          <li key={e.seq} className="leading-snug">
            <span className="text-t-muted">{e.status}{e.latency_ms != null ? `, ${fmtMs(e.latency_ms)}` : ''}</span>
            {e.message && <div className="text-t-text">{e.message}</div>}
            {e.model && <div className="text-t-muted text-xs font-mono">{e.model} on {e.provider}</div>}
            {e.evidence_ids.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{e.evidence_ids.map((id) => <CitationChip key={id} id={id} onClick={onCite} />)}</div>}
          </li>
        ))}
      </ol>
    </div>
  );
}

// ---------------------------------------------------------------- intent / latency
const INTENT_LABEL: Record<string, string> = {
  event_impact: 'Impact of an event', portfolio_risk: 'Portfolio risk', hedge_request: 'Hedge request',
  explain: 'Explain an earlier answer', market_summary: 'Market summary',
};

function IntentRow({ k, children }: { k: string; children: React.ReactNode }) {
  return <div className="grid grid-cols-[96px_1fr] gap-3 py-1.5"><dt className="text-t-muted">{k}</dt><dd className="text-t-text min-w-0">{children}</dd></div>;
}

export function IntentPanel({ intent, events }: { intent: Intent | null; events: AgentEvent[] }) {
  if (!intent) return <Empty>Shows how the question was understood, once the first step finishes.</Empty>;
  const ran = new Set(events.map((e) => e.node.replace(/_agent$/, '')));
  // the model sometimes leaks JSON fragments into a ticker string; show only well-formed symbols
  const tickers = intent.tickers.filter((t) => /^[A-Z0-9^&=._-]{1,20}$/.test(t));
  return (
    <dl className="px-4 py-2 overflow-auto text-[13px]">
      <IntentRow k="Question">{INTENT_LABEL[intent.intent] ?? intent.intent}</IntentRow>
      {intent.event_type && intent.event_type !== 'other' && <IntentRow k="Event">{intent.event_type}</IntentRow>}
      {intent.region && <IntentRow k="Region">{intent.region}</IntentRow>}
      <IntentRow k="Horizon">{intent.horizon_days} days</IntentRow>
      <IntentRow k="Tickers">{tickers.length
        ? <span className="flex flex-wrap gap-1">{tickers.map((t) => <span key={t} className="px-1.5 rounded bg-t-line text-t-text text-xs">{t}</span>)}</span>
        : 'Your portfolio'}</IntentRow>
      {intent.needs_tools.length > 0 && (
        <IntentRow k="Data">{intent.needs_tools.map((t, i) => (
          <span key={t} className={ran.has(t) || ran.has(t.replace(/s$/, '')) ? 'text-t-text' : 'text-t-muted'}>{t}{i < intent.needs_tools.length - 1 ? ', ' : ''}</span>
        ))}</IntentRow>
      )}
    </dl>
  );
}

const STEP_LABEL: Record<string, string> = { fanout: 'All agents' };
export function LatencyWaterfall({ latency }: { latency: Record<string, number> }) {
  const entries = Object.entries(latency).filter(([k]) => k !== 'total');
  const total = latency.total || Math.max(1, ...entries.map(([, v]) => v));
  if (!entries.length) return <Empty>Step timings appear as each step finishes.</Empty>;
  return (
    <div className="flex-1 px-4 py-3 flex flex-col gap-1.5 text-xs overflow-auto">
      {entries.map(([k, v]) => (
        <div key={k} className="grid grid-cols-[110px_1fr_56px] items-center gap-3">
          <span className="truncate text-t-muted">{STEP_LABEL[k] ?? NODE_LABEL[k] ?? k}</span>
          <div className="h-1.5 rounded-full bg-t-line"><div className="h-full rounded-full bg-t-muted/70" style={{ width: `${Math.max(2, Math.min(100, (v / total) * 100))}%` }} /></div>
          <span className="text-t-text text-right">{fmtMs(v)}</span>
        </div>
      ))}
      {latency.total != null && (
        <div className="grid grid-cols-[110px_1fr_56px] gap-3 pt-1.5 mt-1 border-t border-t-fg/[0.07] font-medium">
          <span className="text-t-text">Total</span><span /><span className="text-t-text text-right">{fmtMs(total)}</span>
        </div>
      )}
    </div>
  );
}

/** Per-node latency from finished/degraded events (shown while the run is still going). */
export function eventLatency(events: AgentEvent[]): Record<string, number> {
  const out: Record<string, number> = {};
  for (const e of events) if (e.latency_ms != null && e.status !== 'started') out[e.node] = e.latency_ms;
  return out;
}

// ---------------------------------------------------------------- answer + citations
const TOOL_COLOR: Record<string, string> = {
  weather: '#4cc9f0', agri: '#3ddc97', sentiment: '#ffb000', news: '#ffb000', analogs: '#c084fc', analog: '#c084fc',
  risk: '#ff4d6d', hedge: '#ff4d6d', exposure: '#f472b6', macro: '#facc15', prices: '#94a3b8', scenario: '#fb923c',
};
/** A citation to one evidence item, shown short ("weather 1"); the full id is in the tooltip and the drawer. */
export function CitationChip({ id, onClick }: { id: string; onClick: (id: string) => void }) {
  const m = id.match(/^ev_(.+)_(\d{3})$/);
  const tool = m ? m[1] : id;
  const c = TOOL_COLOR[tool.split('_')[0]] ?? '#94a3b8';
  return (
    <button onClick={() => onClick(id)} title={`Open evidence ${id}`}
      className="inline-flex items-center gap-1 align-baseline text-[11px] leading-none px-1.5 py-[3px] mx-0.5 rounded font-mono text-t-text bg-t-line/70 hover:bg-t-line">
      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: c }} />
      {tool.replace(/_/g, ' ')} {m ? Number(m[2]) : ''}
    </button>
  );
}

function Inline({ text, onCite }: { text: string; onCite: (id: string) => void }) {
  return (
    <>
      {splitCitations(text).map((p, i) =>
        'cite' in p ? <CitationChip key={i} id={p.cite} onClick={onCite} /> : (
          <React.Fragment key={i}>
            {p.text.split(/(\*\*[^*]+\*\*|(?<![A-Za-z0-9])_[^_\n]+_(?![A-Za-z0-9]))/).map((s, j) =>
              s.startsWith('**') ? <strong key={j} className="text-t-fg font-bold">{s.slice(2, -2)}</strong>
                : s.length > 2 && s.startsWith('_') && s.endsWith('_') ? <em key={j} className="text-t-muted">{s.slice(1, -1)}</em>
                : <React.Fragment key={j}>{s}</React.Fragment>)}
          </React.Fragment>
        ))}
    </>
  );
}

export function Markdown({ md, onCite }: { md: string; onCite: (id: string) => void }) {
  const clean = md.replace(/<json>[\s\S]*?<\/json>/g, '').trim();
  return (
    <div className="space-y-2.5">
      {clean.split('\n').map((line, i) => {
        const t = line.trim();
        if (!t) return null;
        if (t.startsWith('#')) return <h4 key={i} className="text-[15px] font-bold text-t-fg pt-4 first:pt-0">{t.replace(/^#+\s*/, '')}</h4>;
        if (/^[-*]\s/.test(t)) return (
          <div key={i} className="pl-5 relative">
            <span className="absolute left-1 top-[0.65em] w-1 h-1 rounded-full bg-t-muted" />
            <Inline text={t.slice(2)} onCite={onCite} />
          </div>
        );
        return <p key={i}><Inline text={t} onCite={onCite} /></p>;
      })}
    </div>
  );
}

const CONF_STYLE: Record<string, string> = {
  high: 'bg-t-mint/15 text-t-mint', medium: 'bg-t-amber/15 text-t-amber', low: 'bg-t-rose/15 text-t-rose',
};
export function ConfidencePill({ c }: { c: string }) {
  return <span className={`text-xs font-bold px-2 py-1 rounded-md whitespace-nowrap ${CONF_STYLE[c] ?? 'bg-t-line text-t-muted'}`}>{c[0]?.toUpperCase() + c.slice(1)} confidence</span>;
}

/** The bottom line is shown large above the answer, so drop it from the markdown (as a section or a leading paragraph). */
function stripBottomLine(md: string, bottom: string) {
  let out = md.replace(/^\s*#+\s*bottom line\s*\n[\s\S]*?(?=\n#)/i, '');
  const b = bottom.trim();
  if (b && out.trimStart().startsWith(b)) out = out.trimStart().slice(b.length);
  return out;
}

export function AnswerBody({ final, onCite }: { final: FinalAnswer; onCite: (id: string) => void }) {
  const degraded = final.evidence.filter((e) => e.degraded && !e.synthetic);
  const simulated = final.evidence.filter((e) => e.synthetic);
  return (
    <div className="px-6 py-5 text-[15px] leading-7 text-t-text flex-1 overflow-auto">
      <div className="max-w-[68ch]">
        {simulated.length > 0 && (
          <div className="mb-5 rounded-lg border border-t-amber/40 bg-t-amber/[0.08] px-4 py-2.5 text-[13px] leading-6">
            <span className="font-bold text-t-amber">Simulated data.</span> This answer uses demo data that is not real
            ({simulated.map((e) => e.tool).join(', ')}). Turn off DEMO_MODE for real data only.
          </div>
        )}
        <p className="mb-5 text-lg leading-8 font-medium text-t-fg"><Inline text={final.bottom_line} onCite={onCite} /></p>
        <Markdown md={stripBottomLine(final.answer_markdown, final.bottom_line)} onCite={onCite} />
        {degraded.length > 0 && (
          <div className="mt-6 rounded-lg border border-t-saffron/30 bg-t-saffron/[0.06] px-4 py-3 text-[13px] leading-6">
            <div className="font-bold text-t-text mb-1">Some data was unavailable, so this answer leans on fallbacks</div>
            <ul className="text-t-muted">
              {degraded.map((e) => (
                <li key={e.id}><CitationChip id={e.id} onClick={onCite} /> {(e.degraded_reason ?? 'degraded').replace(/_/g, ' ')}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

export function EventLog({ events }: { events: AgentEvent[] }) {
  // scroll only this box (scrollIntoView would also scroll the page around it)
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [events.length]);
  const tone = (s: string) => (s === 'failed' ? 'text-t-rose' : s === 'degraded' ? 'text-t-saffron' : s === 'finished' ? 'text-t-mint' : 'text-t-muted');
  return (
    <div ref={box} className="px-4 py-3 font-mono text-xs leading-6 overflow-auto flex-1">
      {events.map((e) => (
        <div key={e.seq} className="grid grid-cols-[64px_120px_72px_1fr] gap-2">
          <span className="text-t-muted">{new Date(e.ts).toLocaleTimeString('en-IN', { hour12: false })}</span>
          <span className="text-t-text truncate">{NODE_LABEL[e.node] ?? e.node}</span>
          <span className={tone(e.status)}>{e.status}</span>
          <span className="text-t-muted truncate" title={e.message ?? ''}>{e.message}</span>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- evidence drawer
function freshness(s?: number | null) {
  if (s == null) return { label: 'Unknown age', c: V('muted') };
  const age = s < 3600 ? `${Math.round(s / 60)} min old` : s < 172800 ? `${Math.round(s / 3600)} h old` : `${Math.round(s / 86400)} days old`;
  if (s < 900) return { label: `Live, ${age}`, c: V('mint') };
  if (s < 6 * 3600) return { label: `Fresh, ${age}`, c: V('amber') };
  if (s < 3 * 86400) return { label: `Stale, ${age}`, c: V('saffron') };
  return { label: `Old, ${age}`, c: V('rose') };
}

export function EvidenceDrawer({ id, evidence, onClose }: { id: string; evidence: Evidence | undefined; onClose: () => void }) {
  const f = freshness(evidence?.freshness_s);
  return (
    <div className="fixed inset-0 z-[100] flex justify-end bg-t-shade/40" onClick={onClose}>
      <div className="w-[480px] max-w-full h-full bg-t-panel2 border-l border-t-fg/10 overflow-auto text-[13px] text-t-text shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 bg-t-panel2 flex justify-between items-start gap-4 px-6 pt-6 pb-4 border-b border-t-fg/[0.07]">
          <div>
            <div className="text-base font-bold text-t-fg">{evidence ? evidence.tool.replace(/_/g, ' ') : 'Evidence'}</div>
            <div className="font-mono text-xs text-t-muted mt-0.5">{id}</div>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-t-muted hover:text-t-fg mt-1"><X size={16} /></button>
        </div>
        {!evidence ? <div className="px-6 py-5 text-t-rose">This evidence is not part of this run.</div> : (
          <div className="px-6 py-5 space-y-3">
            {evidence.synthetic && <div className="rounded-md bg-t-amber/[0.1] text-t-amber text-[13px] font-bold px-3 py-1.5">Simulated demo data, not real</div>}
            {evidence.summary && <p className="text-[15px] leading-6 text-t-text">{evidence.summary}</p>}
            <dl className="space-y-2 pt-1">
              <Row k="Source" v={evidence.source_url ? <a className="underline underline-offset-2" href={evidence.source_url} target="_blank" rel="noreferrer">{evidence.source}</a> : evidence.source} />
              <Row k="Data as of" v={new Date(evidence.as_of).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })} />
              <Row k="Freshness" v={<span style={{ color: f.c }}>{f.label}</span>} />
              <Row k="Confidence" v={evidence.confidence != null ? (
                <div className="flex items-center gap-2"><div className="h-1.5 w-28 rounded-full bg-t-line"><div className="h-full rounded-full bg-t-text/60" style={{ width: `${evidence.confidence * 100}%` }} /></div>{Math.round(evidence.confidence * 100)}%</div>) : '—'} />
              {evidence.degraded && <Row k="Fallback" v={<span className="text-t-saffron">{(evidence.degraded_reason ?? 'yes').replace(/_/g, ' ')}</span>} />}
              <Row k="Took" v={evidence.latency_ms != null ? fmtMs(evidence.latency_ms) : '—'} />
              {evidence.model_version && <Row k="Model" v={evidence.model_version} />}
            </dl>
            <div className="pt-3">
              <div className="text-t-muted mb-1.5">Raw value</div>
              <pre className="font-mono bg-t-shade/[0.06] rounded-md border border-t-fg/[0.07] p-3 whitespace-pre-wrap break-all text-xs leading-5 max-h-[50vh] overflow-auto select-text">{JSON.stringify(evidence.value, null, 2)}</pre>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return <div className="grid grid-cols-[96px_1fr] gap-3"><dt className="text-t-muted">{k}</dt><dd className="break-words">{v}</dd></div>;
}

// ---------------------------------------------------------------- heatmap + price chart
const FACTOR_LABEL: Record<string, string> = { weather: 'Weather', agri: 'Crops', crude: 'Crude', rates: 'Rates', usd_inr: 'USD/INR' };
export function SectorHeatmap({ value }: { value: any | null }) {
  const hm = value?.heatmap;
  if (!hm?.rows?.length) return <Empty>Sector sensitivities load from the quant service.</Empty>;
  const weights: Record<string, number> = hm.row_weights ?? value.by_sector ?? {};
  const cell = (v: number) => {
    const a = Math.min(1, Math.abs(v));
    return v > 0 ? `rgb(var(--c-mint) / ${0.08 + a * 0.5})` : v < 0 ? `rgb(var(--c-rose) / ${0.08 + a * 0.5})` : 'rgb(var(--c-line) / 0.6)';
  };
  const cols = `minmax(96px,1.5fr) repeat(${hm.cols.length}, 1fr)`;
  return (
    <div className="flex-1 px-4 py-3 overflow-auto text-xs">
      <div className="grid text-t-muted mb-1.5 gap-1" style={{ gridTemplateColumns: cols }}>
        <div>Sector, weight</div>{hm.cols.map((c: string) => <div key={c} className="text-center">{FACTOR_LABEL[c] ?? c}</div>)}
      </div>
      <div className="space-y-1">
        {hm.rows.map((r: string, i: number) => (
          <div key={r} className="grid h-7 gap-1" style={{ gridTemplateColumns: cols }}>
            <div className="text-t-text flex items-center truncate">{r}{weights[r] != null && <span className="text-t-muted ml-1.5">{Math.round(weights[r] * 100)}%</span>}</div>
            {hm.values[i].map((v: number, j: number) => (
              <div key={j} className="flex items-center justify-center rounded text-t-text" style={{ background: cell(v) }}>{v.toFixed(1)}</div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

export interface Bar { date: string; open: number; high: number; low: number; close: number }
export function PriceChart({ rows }: { rows: Bar[] }) {
  const ref = useRef<HTMLDivElement>(null);
  const light = useSettings((s) => s.theme) === 'light';   // canvas needs concrete colours, so rebuild on theme change
  useEffect(() => {
    if (!ref.current || !rows.length) return;
    const chart = createChart(ref.current, {
      layout: { background: { color: 'transparent' }, textColor: light ? '#5d6b7c' : '#8b97a5', fontFamily: 'Satoshi, sans-serif', fontSize: 11 },
      grid: { vertLines: { visible: false }, horzLines: { color: light ? '#eef1f4' : '#2f3236' } },
      timeScale: { borderColor: light ? '#d5dbe2' : '#3a3d42' },
      rightPriceScale: { borderColor: light ? '#d5dbe2' : '#3a3d42' },
      crosshair: { mode: 1 },
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
    });
    const s = chart.addCandlestickSeries({ upColor: '#2dbf8f', downColor: '#e5484d', borderVisible: false, wickUpColor: '#2dbf8f', wickDownColor: '#e5484d' });
    const seen = new Set<string>();
    s.setData(rows.filter((r) => r.open != null && !seen.has(r.date) && seen.add(r.date)).map((r) => ({ time: r.date.slice(0, 10) as any, open: r.open, high: r.high, low: r.low, close: r.close })));
    chart.timeScale().fitContent();
    const ro = new ResizeObserver(() => ref.current && chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight }));
    ro.observe(ref.current);
    return () => { ro.disconnect(); chart.remove(); };
  }, [rows, light]);
  if (!rows.length) return <Empty>No price data. Check that the ingestion service (port 8201) is running.</Empty>;
  return <div ref={ref} className="w-full h-full" />;
}
