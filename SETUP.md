# Ingestion service (doc 10): where to put it and what to do

## 1. Where the files go (L3 laptop, in the shared monorepo)
Copy the `services/` folder from this zip into the repo root so you get:

    copilot/services/ingestion/...        <- everything from this zip
    copilot/services/__init__.py          <- already included (empty)
    copilot/data/storms.json              <- copy data/storms.json.example, hand-edit during the demo
    copilot/data/cache/                   <- created automatically (gitignored)

Also: copy `services/ingestion/fixtures/*.json` into `packages/copilot_common/copilot_common/fixtures/ingestion/`
(the contract owner wants them there too; my code reads its own `services/ingestion/fixtures/` for MOCK=1).

## 2. Install (L3)
    pip install -e packages/copilot_common          # from teammate (01) - must exist first
    pip install -r services/ingestion/requirements.txt

## 3. Edit these files by hand (real data, not code)
- `config/policy_rates.json`  real RBI repo history (the 2 rows are the spec's EXAMPLES)
- `config/cpi_india.csv`      real MoSPI CPI YoY (rows are EXAMPLES, marked with a # line)
- `config/rss_feeds.json`     open each URL in a browser on day 0 and delete dead ones (Moneycontrol is unverified)
- `data/storms.json`          IMD cyclone entry for the demo (see the .example file)
- `.env` (L3)                 `SENTIMENT_URL`, `VECTOR_URL` (already in 01 contract). `FRED_API_KEY` is not needed (no-key CSV is used)

## 4. Run (from the REPO ROOT, not from inside the folder)
    uvicorn services.ingestion.app:app --host 0.0.0.0 --port 8201
    curl localhost:8201/health
    MOCK=1 uvicorn ...                      # fixtures only, no internet
    CACHE_MODE=replay uvicorn ...           # offline demo from cache

## 5. Night before the demo
    python -m services.ingestion.prewarm    # caches 15 regions, 16 tickers (2y), macro, 7d news; prints a table
Copy `data/cache/` to L1 for single-laptop mode.

## 6. Test
    pytest services/ingestion/tests
Feature/store/macro tests need nothing else. `test_service.py` and `test_weather_handler.py` need `copilot_common` (auto-skipped if absent).

## 7. Things to check against your teammates' REAL copilot_common (I coded to the contract; I could only test against a stub)
1. `cache.cached(service, key_obj, fn, ttl_s=...)` returns the saved VALUE (not the {"key","saved_at","value"} wrapper) and raises `CacheMiss` in replay.
   I call it with service = `"ingestion/<source>"` to get `data/cache/ingestion/<source>/<sha>.json`.
2. `ids.EvidenceCounter(run_id).next(tool)` and `ids.new_run_id()`.
3. `service_base.create_service_app("ingestion", deps_check=...)`: my `check_deps` works sync or awaited.
4. `ToolResult`/`Evidence` field names exactly as in 01 section 5.

## 8. Verify on day 0 (external APIs I could not call from here)
- Open-Meteo variable names (`openmeteo.py` -> `SOIL_VARS`, `DAILY_*`). ERA5 daily has no RH here, so heat index assumes 60% for `as_of` runs on archive data.
- GDELT limit ~1 request / 5 s (cached; prewarm sleeps).
- NSE announcements may block you; they degrade gracefully (HTTP 200, `degraded=true`).
- NHC `CurrentStorms.json` field names; wrapped in try/except.

## Acceptance checklist (doc 10 section 11)
- weather/macro keys match contract 01 section 6: covered by `test_weather_handler.py`
- replay with empty cache -> degraded, HTTP 200, no network: `test_service.py`
- `as_of` never leaks: `test_as_of_never_leaks`
- chaos `weather_down` -> degraded 200 (confidence x0.5 of last good): both tests
- /prices 10 tickers <5 s cold, <100 ms warm, and prewarm for 15 regions: run on L3 with internet
- worker pushes to sentiment/vectordb, outage only degrades /health: run with 07/08 up, then kill them
