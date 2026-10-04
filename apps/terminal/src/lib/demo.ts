'use client';
// Offline demo: what the terminal shows when the backend cannot be reached (pitch safety net).
// Everything here is illustrative, not live data. The UI labels it "Demo data" wherever it appears.
import { useEffect, useState } from 'react';
import type { AgentEvent, Evidence, FinalAnswer, Portfolio } from './contracts';

/** One calm sentence instead of a raw error ("Failed to fetch", stack traces, log paths). */
export const FRIENDLY = {
  generic: 'This part is taking a short break. Showing what we have.',
  paper: 'Paper trading is not available right now.',
  backtest: 'Backtest results will appear here once the latest run is loaded.',
  cluster: 'Live cluster status will appear here once the services report in.',
  scenario: 'The scenario engine is warming up. Try the slider again in a moment.',
  save: 'Could not save just now. Your edits are still here; try again in a moment.',
  csv: 'That file could not be read. Check that it has ticker and qty columns.',
  hedge: 'Paper trading is not available right now.',
};

// Same holdings as data/portfolio_demo.csv
export const DEMO_PORTFOLIO: Portfolio = {
  portfolio_id: 'demo', cash: 0, currency: 'INR',
  holdings: [
    { ticker: 'RELIANCE.NS', qty: 50, avg_price: 2850, sector: 'Energy' },
    { ticker: 'ONGC.NS', qty: 600, avg_price: 255, sector: 'Oil&Gas' },
    { ticker: 'COALINDIA.NS', qty: 700, avg_price: 410, sector: 'Energy' },
    { ticker: 'NTPC.NS', qty: 500, avg_price: 355, sector: 'Utilities' },
    { ticker: 'ITC.NS', qty: 450, avg_price: 430, sector: 'FMCG' },
    { ticker: 'HINDUNILVR.NS', qty: 50, avg_price: 2450, sector: 'FMCG' },
    { ticker: 'HDFCBANK.NS', qty: 120, avg_price: 1850, sector: 'Banks' },
    { ticker: 'UPL.NS', qty: 230, avg_price: 650, sector: 'Agri' },
  ],
};

type Scenario = {
  key: string; match: RegExp; event_type: string; region: string | null; agents: string[];
  bottom: string; body: string; ev: [string, string, string][];   // [id, tool, summary]
  hedges: FinalAnswer['hedges']; reasons: string[];
};

const H_NIFTY: FinalAnswer['hedges'][number] = {
  hedge_id: 'h1', instrument: 'NIFTY OCT FUT short', underlying: '^NSEI', side: 'sell', quantity: 1, unit: 'lots',
  hedge_ratio: 0.85, est_cost_inr: null, rationale: 'Index future offsets the market part of the drawdown',
  sizing_method: 'min_variance', evidence_ids: ['ev_hedge_001'],
};

const SCENARIOS: Scenario[] = [
  {
    key: 'weather', match: /cyclone|monsoon|rain|flood|storm|odisha|bay of bengal|fmcg|crop|agri/i,
    event_type: 'cyclone', region: 'Odisha',
    agents: ['sentiment_agent', 'weather_agent', 'agri_agent', 'analog_agent', 'exposure_agent'],
    bottom: 'A cyclone during a weak monsoon mainly hits your FMCG and agri names through rural demand and input costs; '
      + 'past cyclones moved these holdings modestly over five days [ev_analogs_001].',
    body: '### Impact on your holdings\n'
      + '- ITC.NS and HINDUNILVR.NS: rural demand softens when kharif sowing is late [ev_agri_001].\n'
      + '- UPL.NS: crop-protection demand tends to rise after a weak start to the season [ev_agri_001].\n'
      + '- Energy and utilities: little direct exposure to the storm track [ev_exposure_001].\n\n'
      + '### What to watch\n- Rainfall over the next two weeks and the storm\'s landfall point [ev_weather_001].',
    ev: [['ev_weather_001', 'weather', 'Storm track and rainfall outlook for the east coast'],
      ['ev_agri_001', 'agri', 'Crop stress outlook for the covered rain-fed districts'],
      ['ev_analogs_001', 'analogs', 'Past cyclones: Phailin 2013, Fani 2019, Yaas 2021'],
      ['ev_exposure_001', 'exposure', 'Sector weights: Energy, FMCG, Utilities, Agri']],
    hedges: [H_NIFTY],
    reasons: ['Past storms differ in track and timing', 'Monsoon recovery later in the season can reverse the effect'],
  },
  {
    key: 'oil', match: /crude|oil|opec|brent|hurricane|louisiana|refiner/i,
    event_type: 'oil', region: null,
    agents: ['sentiment_agent', 'macro_agent', 'analog_agent', 'exposure_agent'],
    bottom: 'A crude spike helps your upstream holding (ONGC) and pressures refiners and consumers of fuel; '
      + 'the net effect on this portfolio is small because the exposures partly offset [ev_exposure_001].',
    body: '### Impact on your holdings\n'
      + '- ONGC.NS: higher realisations, historically positive over five days [ev_analogs_001].\n'
      + '- RELIANCE.NS: refining margins squeeze in the short term [ev_analogs_001].\n'
      + '- FMCG names: input-cost pressure if the move lasts [ev_macro_001].\n\n'
      + '### What to watch\n- Whether the move holds beyond a week, and the rupee [ev_macro_001].',
    ev: [['ev_macro_001', 'macro', 'Brent, USD/INR and policy rate snapshot'],
      ['ev_analogs_001', 'analogs', 'Past oil shocks: OPEC cut 2023, Abqaiq 2019'],
      ['ev_exposure_001', 'exposure', 'Energy and oil-linked weight of the portfolio']],
    hedges: [H_NIFTY],
    reasons: ['Supply shocks often fade within weeks', 'The rupee can amplify or offset the move'],
  },
  {
    key: 'rates', match: /rbi|repo|rate|bps|bank|inflation/i,
    event_type: 'rates', region: null,
    agents: ['sentiment_agent', 'macro_agent', 'analog_agent', 'exposure_agent'],
    bottom: 'A surprise repo hike mostly affects your bank holding and rate-sensitive sectors in the first week; '
      + 'past hikes saw banks react first and recover as margins re-priced [ev_analogs_001].',
    body: '### Impact on your holdings\n'
      + '- HDFCBANK.NS: first reaction usually negative, then margin benefit [ev_analogs_001].\n'
      + '- Utilities: higher funding costs weigh on valuations [ev_exposure_001].\n\n'
      + '### What to watch\n- RBI guidance and the inflation print [ev_macro_001].',
    ev: [['ev_macro_001', 'macro', 'Repo rate history and latest CPI'],
      ['ev_analogs_001', 'analogs', 'Past RBI moves: 2022 off-cycle hike, 2020 emergency cut'],
      ['ev_exposure_001', 'exposure', 'Rate-sensitive weight of the portfolio']],
    hedges: [H_NIFTY],
    reasons: ['The market may already price part of the move', 'Guidance matters more than the hike itself'],
  },
  {
    key: 'general', match: /.*/,
    event_type: 'other', region: null,
    agents: ['sentiment_agent', 'macro_agent', 'analog_agent', 'exposure_agent'],
    bottom: 'Your portfolio leans on energy and FMCG, so oil prices and rural demand are the two things that move it most '
      + '[ev_exposure_001].',
    body: '### Where the risk sits\n'
      + '- Energy and utilities are the largest weights [ev_exposure_001].\n'
      + '- FMCG and agri respond to the monsoon and rural demand [ev_analogs_001].\n\n'
      + '### What to watch\n- Crude, the monsoon, and the RBI\'s next decision [ev_macro_001].',
    ev: [['ev_exposure_001', 'exposure', 'Sector weights of the portfolio'],
      ['ev_analogs_001', 'analogs', 'Similar past market events'],
      ['ev_macro_001', 'macro', 'Macro snapshot: crude, rupee, policy rate']],
    hedges: [H_NIFTY],
    reasons: ['Sector weights change as prices move', 'One event rarely acts alone'],
  },
];

const GREETING = /^\s*(hi+|hello+|hey+|namaste|good (morning|evening|afternoon)|thanks?|thank you|help)\b/i;
const SUGGEST = [
  'A cyclone in the Bay of Bengal and a weak monsoon: what happens to my FMCG and agri stocks?',
  'Crude is up 10% this week. Which holdings get hurt, and how should I hedge?',
  'If the RBI raises the repo rate by 25 bps, how exposed are my bank holdings?',
];

function evidence(runId: string, [id, tool, summary]: [string, string, string]): Evidence {
  const t = new Date().toISOString();
  return { id, run_id: runId, tool, value: {}, summary, source: 'offline demo', as_of: t, timestamp: t,
           confidence: 0.6, degraded: false };
}

export function demoFinal(query: string, runId: string): FinalAnswer {
  const base = { run_id: runId, query, lang: 'en', llm_usage: {}, what_could_be_wrong: [], signals: [],
                 holdings_impact: [] as Record<string, any>[] };
  const pass = { numbers_found: 0, numbers_matched: 0, unmatched: [], action: 'pass' as const, auto_cited: [] };
  if (GREETING.test(query)) {
    const text = 'Hi! I look at how events like storms, oil shocks and rate moves could affect your portfolio. Try one of these:';
    return { ...base, kind: 'conversation', suggestions: SUGGEST, bottom_line: text, answer_markdown: text,
             intent: { intent: 'greeting', tickers: [], asset_classes: [], horizon_days: 5, references_portfolio: false, needs_tools: [] },
             hedges: [], confidence: 'high', red_team: null, validator: pass, evidence: [], latency_ms: { total: 600 } };
  }
  const s = SCENARIOS.find((x) => x.match.test(query))!;
  const ev = [...s.ev, ['ev_hedge_001', 'hedge', 'Hedge sizing on 5-day returns'] as [string, string, string]];
  return {
    ...base, kind: 'analysis',
    intent: { intent: 'event_impact', event_type: s.event_type, region: s.region, tickers: [], asset_classes: ['equity'],
              horizon_days: 5, references_portfolio: true, needs_tools: [] },
    bottom_line: s.bottom,
    answer_markdown: `### Bottom line\n${s.bottom}\n\n${s.body}`,
    hedges: s.hedges, confidence: 'medium',
    red_team: { reasons: s.reasons, verdict: 'proceed with caution', verdict_reason: 'Illustrative scenario' },
    validator: pass, evidence: ev.map((e) => evidence(runId, e)),
    latency_ms: { total: 6200 },
  };
}

function demoEvents(query: string, runId: string): AgentEvent[] {
  if (GREETING.test(query)) return [];
  const s = SCENARIOS.find((x) => x.match.test(query))!;
  const order = ['parse_intent', 'router', ...s.agents, 'join', 'quant_agent', 'synthesizer', 'red_team', 'validator'];
  const out: AgentEvent[] = [];
  let t = 0;
  let seq = 0;
  const ev = (node: string, status: AgentEvent['status'], latency?: number, message?: string): AgentEvent => ({
    run_id: runId, seq: ++seq, node, status, ts: new Date().toISOString(), t_ms: t, latency_ms: latency ?? null,
    evidence_ids: [], message: message ?? null, meta: {},
  });
  for (const node of order) {
    const isAgent = s.agents.includes(node);
    if (!isAgent || node === s.agents[0]) {                // agents start together, like the real fan-out
      for (const a of isAgent ? s.agents : [node]) out.push(ev(a, 'started'));
    }
    const dur = node === 'synthesizer' ? 1600 : isAgent ? 500 + 150 * s.agents.indexOf(node) : 300;
    t += isAgent ? (node === s.agents[s.agents.length - 1] ? dur : 0) : dur;
    out.push(ev(node, 'finished', dur));
  }
  return out;
}

/** Replays a demo run over ~5 s so the agent graph fills in like a live one. Inactive when query is null. */
export function useDemoStream(query: string | null) {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [final, setFinal] = useState<FinalAnswer | null>(null);
  useEffect(() => {
    setEvents([]); setFinal(null);
    if (query == null) return;
    const runId = `demo_${Date.now().toString(36)}`;
    const all = demoEvents(query, runId);
    const timers = all.map((e, i) => setTimeout(() => setEvents((p) => [...p, e]), 250 + i * 220));
    timers.push(setTimeout(() => setFinal(demoFinal(query, runId)), 400 + all.length * 220));
    return () => timers.forEach(clearTimeout);
  }, [query]);
  return { events, final, status: final ? 'done' : 'running' };
}
