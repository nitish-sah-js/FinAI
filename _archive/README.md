# _archive

Source copies that the live system no longer imports, kept for reference (moved 2026-10-04).

| Folder | What it was | Replaced by |
|---|---|---|
| agri/ | original crop-model training scripts and data | services/agri |
| monitor/ | first monitor prototype | services/monitor |
| db_nitr/ | older vector DB + copilot_common copy | services/vectordb, packages/copilot_common |
| backtester_nitr/ | original backtester | backtest/, services/orchestrator/paper |
| copilot/ | early quant service + stand-in copilot_common | services/quant |
| frontend/ | original "Utsava Terminal" UI with mock data | apps/terminal |
| terminal-stubs/ | empty component/lib stubs from the original UI | components/panels, home, views |
| terminal-scripts/ | one-off encoding/patch scripts | — |
| git-history/ | the original frontend repo's .git folder (git-ignored here) | — |

Nothing outside _archive imports from here.
