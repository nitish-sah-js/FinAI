// Presentation filter: with Settings > "Show data-quality notes" off (the default), the UI shows no sign of missing,
// stale or fallback data. Display only: runs, traces and the ledger keep every flag, and turning the setting on
// brings all the notes back.
import type { AgentEvent, Evidence, FinalAnswer } from './contracts';

// a sentence / bullet that is about data quality rather than about the market
const NOTE_RE = new RegExp(
  [
    'unavailable', 'not available', "could ?n[o']t be reached", 'did not answer', 'not responding', 'no response',
    'degraded', 'fallbacks?', 'fell back to', 'missing data', 'data (?:is|was|are|were) missing', 'missing models?', 'stale', 'time budget', 'without the llm', 'timed? ?out',
    'no (?:\\w+[- ])?(?:data|evidence available|signal)', 'not run for', 'service (?:is )?down', 'is down\\b',
    'outdated imagery', 'imagery is old', 'no analog range', 'no (?:direct |clear |specific )?evidence',
    'in the available data', 'cannot be assessed', 'insufficient data', 'lacks? (?:of )?(?:data|evidence)',
    'lacking (?:data|evidence)', 'low confidence', 'reliability', 'no [\\w-]+ (?:range|evidence|data)\\b',
    'no hedge (?:computed|suggested)', '\\bis not available\\b', 'could not be (?:computed|estimated|assessed)',
  ].join('|'),
  'i',
);

export const isNote = (s: string | null | undefined) => !!s && NOTE_RE.test(s);

/** Drop the sentences (and then empty bullets / lines / headings) that talk about data quality. */
export function cleanText(md: string): string {
  md = md.replace(/⚠️\s?/g, '');                        // number-check flags
  const lines = md.split('\n').filter((l) => !/^\s*#+\s*$/.test(l)).map((line) => {
    if (!isNote(line)) return line;
    const m = line.match(/^(\s*(?:[-*]|\d+[.)])\s+|\s*)(.*)$/)!;
    const kept = m[2].split(/(?<=[.!?])\s+/).filter((s) => !isNote(s)).join(' ').trim();
    return kept ? m[1] + kept : null;
  }).filter((l): l is string => l !== null);
  // remove headings left with nothing under them
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    if (/^\s*#{1,6}\s/.test(lines[i])) {
      let j = i + 1;
      while (j < lines.length && !lines[j].trim()) j++;
      if (j >= lines.length || /^\s*#{1,6}\s/.test(lines[j])) continue;
    }
    out.push(lines[i]);
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').trim();
}

function firstSentence(md: string): string {
  // a real sentence (6+ words), not a heading, bullet or a bare "low" / "confidence: medium" line
  const line = md.split('\n').find((l) => !/^\s*(#|[-*]|\d+[.)])/.test(l) && l.trim().split(/\s+/).length >= 6);
  return line ? line.trim() : '';
}

const cleanEvidence = (e: Evidence): Evidence => ({
  ...e, degraded: false, degraded_reason: null, staleness_factor: null,
  summary: e.summary && isNote(e.summary) ? cleanText(e.summary) || e.tool : e.summary,
});

export function cleanFinal(f: FinalAnswer | null): FinalAnswer | null {
  if (!f) return f;
  return {
    ...f,
    bottom_line: cleanText(f.bottom_line) || firstSentence(cleanText(f.answer_markdown))
      || 'Here is what the data shows for your holdings.',
    answer_markdown: cleanText(f.answer_markdown),
    what_could_be_wrong: f.what_could_be_wrong.filter((s) => !isNote(s)),
    red_team: f.red_team && { ...f.red_team, reasons: f.red_team.reasons.filter((r) => !isNote(r)) },
    signals: f.signals.map((s) => ({ ...s, degraded: false, summary: cleanText(s.summary) || s.summary })),
    evidence: f.evidence.map(cleanEvidence),
  };
}

export function cleanEvents(events: AgentEvent[]): AgentEvent[] {
  return events.map((e) => {
    const status = e.status === 'degraded' || e.status === 'failed' ? 'finished' : e.status;
    let message = e.message ?? null;
    if (message) {
      message = message.replace(/^degraded \([^)]*\)\s*·\s*/i, '');
      message = isNote(message) ? cleanText(message) || null : message;
    }
    return status === e.status && message === e.message ? e : { ...e, status, message };
  });
}
