import json, re, unicodedata, asyncio, httpx, time
from pathlib import Path

from copilot_common.settings import get_settings

DATA_DIR   = get_settings().data_dir     # repo-root data/ (01 §2), not services/data
NSE_MAX_AGE_S = 7 * 86400               # the NSE equity list changes slowly; refresh weekly, never block on it
NSE_CACHE  = DATA_DIR / "nse_symbols.json"
ALIAS_FILE = DATA_DIR / "ticker_aliases.json"

NSE_URLS = [
    "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "https://archives.nseindia.com/content/equities/SME_EQUITY_L.csv",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer":    "https://www.nseindia.com/",
    "Accept":     "text/html,application/xhtml+xml,*/*",
}
STRIP = re.compile(
    r'\b(limited|ltd|inc|corp|corporation|industries|enterprise|enterprises|'
    r'finance|bank|technologies|technology|services|solutions|group|india|'
    r'holdings|ventures|infotech|systems|international|infra|infratech)\b',
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    s = s.lower().strip()
    s = unicodedata.normalize("NFKD", s)
    s = re.sub(r'[^\x00-\x7f]', '', s)
    s = STRIP.sub('', s)
    s = re.sub(r'[^a-z0-9\s&]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()


def _read_cache() -> dict[str, dict]:
    try:
        with open(NSE_CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


async def _fetch_nse() -> dict[str, dict]:
    cached = _read_cache() if NSE_CACHE.exists() else {}
    if cached and time.time() - NSE_CACHE.stat().st_mtime < NSE_MAX_AGE_S:
        print(f"[ticker_map] {len(cached)} tickers from cache")
        return cached

    result = {}
    async with httpx.AsyncClient(
            headers=HEADERS, timeout=30,
            follow_redirects=True) as client:
        for url in NSE_URLS:
            try:
                r = await client.get(url)
                r.raise_for_status()
                lines = r.text.strip().splitlines()
                hdr = [h.strip().upper() for h in lines[0].split(",")]
                si = next(i for i, h in enumerate(hdr) if "SYMBOL" in h)
                ni = next(i for i, h in enumerate(hdr) if "NAME"   in h)
                ii = next((i for i,h in enumerate(hdr) if "ISIN"   in h), None)
                for line in lines[1:]:
                    p = line.split(",")
                    if len(p) <= max(si, ni):
                        continue
                    sym  = p[si].strip()
                    name = p[ni].strip()
                    if not sym or not name:
                        continue
                    key = f"{sym}.NS"
                    result[key] = {
                        "symbol":    sym,
                        "name":      name,
                        "name_norm": _norm(name),
                        "isin":      p[ii].strip() if ii else "",
                    }
                print(f"[ticker_map] {len(result)} tickers from NSE")
            except Exception as e:
                print(f"[ticker_map] fetch failed: {e}")

    if result:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(NSE_CACHE, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        return result
    if cached:   # NSE blocks scripted downloads often: a stale list beats an empty index
        print(f"[ticker_map] NSE refresh failed; using stale cache ({len(cached)} tickers)")
    return cached


def _light(s: str) -> str:
    """Lower-case + punctuation→space, WITHOUT stripping words: curated aliases like 'hdfc bank' or 'coal india'
    must keep 'bank'/'india' (the full _norm would reduce them to the ambiguous 'hdfc'/'coal')."""
    s = re.sub(r"[^a-z0-9&\s]", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


class TickerIndex:
    def __init__(self):
        self._data:    dict[str, dict]      = {}
        self._exact:   dict[str, str]       = {}     # multi-word normalised company name → ticker
        self._single:  dict[str, str]       = {}     # one-word normalised name ("rain", "infosys") → ticker
        self._symbols: dict[str, str]       = {}     # NSE symbol (upper case) → ticker
        self._tokens:  dict[str, list[str]] = {}     # token → tickers, multi-word names only
        self._ntoks:   dict[str, int]       = {}     # ticker → number of tokens in its name
        self._aliases: dict[str, str]       = {}     # curated alias (light-normalised) → ticker
        self._isin:    dict[str, str]       = {}
        self.ready = False

    async def build(self):
        self._data = await _fetch_nse()

        user_aliases: dict[str, list[str]] = {}
        if ALIAS_FILE.exists() and ALIAS_FILE.stat().st_size > 0:
            try:
                with open(ALIAS_FILE, encoding="utf-8") as f:
                    user_aliases = json.load(f)
            except ValueError as e:
                print(f"[ticker_map] {ALIAS_FILE.name} is not valid JSON ({e}); ignoring it")

        for key, rec in self._data.items():
            norm = rec["name_norm"]
            if len(norm) > 2:
                (self._exact if len(norm.split()) >= 2 else self._single)[norm] = key
            if len(rec["symbol"]) >= 3:
                self._symbols[rec["symbol"].upper()] = key
            if rec.get("isin"):
                self._isin[rec["isin"]] = key
            toks = [t for t in norm.split() if len(t) >= 4]
            if len(toks) >= 2:
                self._ntoks[key] = len(toks)
                for tok in toks:
                    self._tokens.setdefault(tok, []).append(key)

        for key, aliases in user_aliases.items():
            for a in aliases:
                self._aliases[_light(a)] = key

        self.ready = True
        print(f"[ticker_map] index: {len(self._data)} tickers "
              f"| {len(self._aliases)} curated aliases "
              f"| {len(self._tokens)} tokens")

    def search(self, text: str, explicit: list[str]) -> list[tuple[str, float]]:
        """Link a headline to tickers. Rules found necessary on real headlines (2026-10-03):
        - one-word names only when Capitalised ("heavy rain" must not link to Rain Industries, RAIN.NS)
        - symbols only in upper case ("ITC", "INFY"); lower-case words are not tickers
        - partial name matches need ≥ 2 shared tokens ("Odisha coast" must not link to Blue Coast Hotels)"""
        assert self.ready
        found: dict[str, float] = {}
        tnorm = _norm(text)
        toks  = set(tnorm.split())

        def put(key: str, s: float) -> None:
            if found.get(key, 0) < s:
                found[key] = s

        for t in explicit:
            found[t] = 1.0

        padded_tnorm = f" {tnorm} "
        padded_light = f" {_light(text)} "

        for isin, key in self._isin.items():
            if isin in text:
                put(key, 1.0)

        for alias, key in self._aliases.items():              # curated: highest trust
            if alias and f" {alias} " in padded_light:
                put(key, 1.0)

        for norm_name, key in self._exact.items():
            if f" {norm_name} " in padded_tnorm:
                put(key, 1.0)

        light_toks = set(padded_light.split())
        for word in toks & self._single.keys():               # pre-filter: only words present in the text
            if re.search(rf"\b{re.escape(word.capitalize())}\b|\b{re.escape(word.upper())}\b", text):
                put(self._single[word], 0.8)

        for tok in light_toks:
            key = self._symbols.get(tok.upper())
            if key and re.search(rf"(?<![A-Za-z0-9]){re.escape(tok.upper())}(?:\.NS)?(?![A-Za-z0-9])", text):
                put(key, 0.9)

        overlap: dict[str, int] = {}
        for tok in toks:
            for key in self._tokens.get(tok, []):
                overlap[key] = overlap.get(key, 0) + 1
        for key, n in overlap.items():
            if n >= 2 and key not in found:
                put(key, 0.5)

        # keep only high-confidence or, if none, whatever was found
        high = {k: v for k, v in found.items() if v >= 0.7}
        final = high if high else found
        return sorted(final.items(), key=lambda x: -x[1])


_index: TickerIndex | None = None


async def get_index() -> TickerIndex:
    global _index
    if _index is None:
        _index = TickerIndex()
        await _index.build()
    return _index


async def search_tickers(
        text: str,
        explicit: list[str] | None = None) -> list[tuple[str, float]]:
    if explicit is None:
        explicit = []
    idx = await get_index()
    return idx.search(text, explicit)