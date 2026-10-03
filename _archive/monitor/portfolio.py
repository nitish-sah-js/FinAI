import pandas as pd
import json

def load_portfolio():
    # Stub: load weights from CSV or Orchestrator
    # Returns dict[ticker, float] weights
    return {"RELIANCE.NS": 0.12, "TCS.NS": 0.08, "ITC.NS": 0.06, "ONGC.NS": 0.04, "ADANIPORTS.NS": 0.05}

def region_links():
    # Stub: load from region_exposure.json
    return {"OD-Puri": [("ADANIPORTS.NS", 0.8), ("ONGC.NS", 0.5)]}
