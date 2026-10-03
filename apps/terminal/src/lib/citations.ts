// Split answer text into plain parts and [ev_tool_nnn] citation ids (docs/13 Prompt 13-B §4).
export const CITATION_RE = /\[(ev_[a-z0-9_]+?_\d{3})\]/g;

export type Part = { text: string } | { cite: string };

export function splitCitations(text: string): Part[] {
  const out: Part[] = [];
  let last = 0;
  for (const m of text.matchAll(CITATION_RE)) {
    if (m.index! > last) out.push({ text: text.slice(last, m.index) });
    out.push({ cite: m[1] });
    last = m.index! + m[0].length;
  }
  if (last < text.length) out.push({ text: text.slice(last) });
  return out;
}
