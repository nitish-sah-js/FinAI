"""Add to services/orchestrator/main.py"""
import asyncio, json, os
from fastapi import FastAPI
from paper.router import router as paper_router
from paper.scheduler import run_scheduler

app = FastAPI()
app.include_router(paper_router, prefix="/paper")


@app.on_event("startup")
async def _start():
    asyncio.create_task(run_scheduler())


@app.get("/backtest/scoreboard")
async def scoreboard():
    path = "packages/copilot_common/copilot_common/fixtures/orchestrator/scoreboard.json" if os.getenv("MOCK") == "1" else "data/backtest/scoreboard.json"
    if not os.path.exists(path):
        return {"status": "not_run"}
    return json.load(open(path))
