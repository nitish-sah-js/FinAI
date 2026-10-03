from pydantic import BaseModel

class Candidate(BaseModel):
    kind: str
    tickers: list[str]
    severity: float
    relevance: float
    confidence: float
    facts: dict
    evidence_ids: list[str]
