"""Ingestion service (L3, port 8201).  Run from repo root:  uvicorn services.ingestion.app:app --port 8201"""
from __future__ import annotations
from contextlib import asynccontextmanager

from fastapi import Request
from pydantic import BaseModel, model_validator
from copilot_common.models import ChaosFlags, ToolResult
from copilot_common.service_base import create_service_app

from . import handlers as H, worker
from .timeutil import parse_as_of
from .util import guarded, load_json

app = create_service_app("ingestion", deps_check=worker.check_deps)

# start/stop background worker without clobbering whatever lifespan service_base installed
_orig = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(a):
    async with _orig(a):
        tasks = worker.start()
        try:
            yield
        finally:
            worker.stop(tasks)


app.router.lifespan_context = _lifespan


class PricesReq(BaseModel):
    tickers: list[str] = []
    ticker: str | None = None            # single-ticker alias (backtester/copilot_common.prices send it)
    period: str = "1y"
    interval: str = "1d"
    start: str | None = None             # YYYY-MM-DD; overrides period (quant/copilot_common.prices send start/end)
    end: str | None = None               # YYYY-MM-DD, inclusive
    as_of: str | None = None
    chaos: ChaosFlags = ChaosFlags()

    @model_validator(mode="after")
    def _one_ticker(self):
        if not self.tickers and self.ticker:
            self.tickers = [self.ticker]
        if not self.tickers:
            raise ValueError("tickers (or ticker) required")
        return self


class NewsReq(BaseModel):
    query: str | None = None
    tickers: list[str] = []
    since_hours: int = 48
    limit: int = 30
    as_of: str | None = None
    chaos: ChaosFlags = ChaosFlags()


class WeatherReq(BaseModel):
    region_id: str | None = None
    lat: float | None = None
    lon: float | None = None
    horizon_days: int = 5
    as_of: str | None = None
    chaos: ChaosFlags = ChaosFlags()


class MacroReq(BaseModel):
    as_of: str | None = None
    chaos: ChaosFlags = ChaosFlags()


class AnnReq(BaseModel):
    tickers: list[str]
    since_hours: int = 72
    as_of: str | None = None
    chaos: ChaosFlags = ChaosFlags()


@app.post("/prices", response_model=ToolResult)
async def prices(req: PricesReq, request: Request):
    return await guarded(request, req.chaos, "prices", "prices", ",".join(req.tickers), "yfinance",
                         lambda rid, c, t0, ch: H.prices_handler(req, rid, c, t0, ch),
                         as_of=parse_as_of(req.as_of))


@app.post("/news", response_model=ToolResult)
async def news(req: NewsReq, request: Request):
    return await guarded(request, req.chaos, "news", "news", req.query or "", "RSS + GDELT",
                         lambda rid, c, t0, ch: H.news_handler(req, rid, c, t0, ch),
                         as_of=parse_as_of(req.as_of))


@app.post("/weather/features", response_model=ToolResult)
async def weather(req: WeatherReq, request: Request):
    key = req.region_id or f"{req.lat},{req.lon}"
    return await guarded(request, req.chaos, "weather", "weather_features", key, "Open-Meteo",
                         lambda rid, c, t0, ch: H.weather_handler(req, rid, c, t0, ch),
                         as_of=parse_as_of(req.as_of))


@app.post("/macro/features", response_model=ToolResult)
async def macro_features(req: MacroReq, request: Request):
    return await guarded(request, req.chaos, "macro", "macro_features", "global", "yfinance/FRED/manual",
                         lambda rid, c, t0, ch: H.macro_handler(req, rid, c, t0, ch),
                         as_of=parse_as_of(req.as_of))


@app.post("/announcements", response_model=ToolResult)
async def announcements(req: AnnReq, request: Request):
    return await guarded(request, req.chaos, "news", "announcements", ",".join(req.tickers), "NSE",
                         lambda rid, c, t0, ch: H.announcements_handler(req, rid, c, t0, ch),
                         as_of=parse_as_of(req.as_of))


@app.get("/feed/since")
async def feed_since(ts: str | None = None):
    return worker.feed_since(ts)


@app.get("/regions")
async def regions():
    return load_json("regions.json")
