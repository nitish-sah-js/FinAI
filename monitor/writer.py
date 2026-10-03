from copilot_common import copilot_llm

async def write_headline(c, weight):
    try:
        return await copilot_llm.chat(role="alert_writer", messages=[{"role":"user","content":str(c.facts)}], max_tokens=60)
    except Exception:
        return f"{c.tickers[0]}: {c.kind} ({c.facts}); {weight:.0%} of portfolio; confidence {c.confidence}."

def reason_from_facts(c):
    return str(c.facts)
