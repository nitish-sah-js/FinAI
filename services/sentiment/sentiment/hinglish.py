import re
import json
import asyncio
import httpx
from pathlib import Path

from copilot_common.settings import get_settings

DATA_DIR = get_settings().data_dir           # repo-root data/ (01 §2), not services/data
HINDI_CACHE = DATA_DIR / "hindi_words.json"

SOURCES = [
    "https://raw.githubusercontent.com/anoopkdcs/hinglish_detection/master/data/hinglish_words.txt",
]

CORE_HINDI = {
    "hai","hain","tha","thi","the","hoga","hogi","hua","hui","hue",
    "kar","karo","karna","kiya","karenge","karega","le","lo","lena",
    "dena","deta","milna","raha","rahi","badhna","girega","uthega",
    "main","hum","tum","aap","yeh","woh","unka","apna","iska","uska",
    "kya","kab","kyun","kaun","kaise","kitna","kahan","aur","lekin",
    "par","ya","agar","toh","ki","ke","ka","se","mein","pe","tak",
    "liye","saath","bhi","nahi","phir","ab","abhi","pehle","baad",
    "bazaar","bazar","girawat","tezi","uthaan","nivesh","munafa",
    "nuksan","khareed","bikri","daam","bhav","sasta","mehnga",
    "mandi","aaj","kal","gaya","gayi","gaye","gir","gira","giri","chadha","chadhi","ank","mazboot","kamzor",
    "dabav","asar","kami","badhat","jyada","zyada","kam","acha","accha","bura","hoga","chahiye","wala","wali",
}

# Words that are also everyday English (or market jargon used in English headlines). Counting them flagged
# "Nifty ends flat as investors await RBI policy" as Hinglish and sent plain English to the slow second opinion.
ENGLISH_COLLISIONS = {
    "the", "main", "le", "lo", "ya", "par", "pe", "hue", "hum", "share", "shares", "broker", "trader", "investor",
    "sensex", "nifty", "to", "a", "an", "in", "on", "is", "it", "do", "so", "no", "us", "we", "me", "he", "be",
}
MIN_RATIO = 0.15      # doc 07 §3.3
MIN_HITS = 2          # one stray word is not a language

_hindi_words: set[str] = set()


async def init_hinglish():
    global _hindi_words
    words = set(CORE_HINDI)

    if HINDI_CACHE.exists():
        try:
            with open(HINDI_CACHE, encoding="utf-8") as f:
                words.update(json.load(f))
        except (OSError, ValueError):
            pass
        _hindi_words = words - ENGLISH_COLLISIONS
        print(f"[hinglish] {len(_hindi_words)} words loaded from cache")
        return

    async with httpx.AsyncClient(timeout=15) as client:
        for url in SOURCES:
            try:
                r = await client.get(url)
                r.raise_for_status()
                for line in r.text.splitlines():
                    w = line.strip().lower()
                    if w and w.isalpha():
                        words.add(w)
                print(f"[hinglish] fetched from {url}")
            except Exception as e:
                print(f"[hinglish] fetch failed {url}: {e}")

    _hindi_words = words - ENGLISH_COLLISIONS
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(HINDI_CACHE, "w", encoding="utf-8") as f:
        json.dump(sorted(_hindi_words), f)
    print(f"[hinglish] {len(_hindi_words)} words cached")


def is_hinglish(text: str) -> bool:
    # 1. Devanagari script present
    if re.search(r'[\u0900-\u097F]', text):
        return True

    # 2. Roman-Hindi token ratio
    tokens = re.findall(r'\b[a-zA-Z]+\b', text.lower())
    words = (_hindi_words or set(CORE_HINDI)) - ENGLISH_COLLISIONS
    if tokens:
        hits = sum(1 for t in tokens if t in words)
        if hits >= MIN_HITS and hits / len(tokens) >= MIN_RATIO:
            return True

    # 3. langid fallback
    try:
        import langid
        lang, conf = langid.classify(text)
        if lang == "hi" and conf > 0.5:
            return True
    except Exception:
        pass

    return False