# copilot_common (minimal stand-in)

Implements the 01_CONTRACTS.md §5 models verbatim plus just enough of ids / settings / cache /
service_base for `services/quant` to run. Replace with the full package when the team lead builds it;
the public names used by quant are: `models.*`, `ids.EvidenceCounter`, `settings.settings`,
`cache.cached/CacheMiss`, `service_base.create_service_app/mock_or/degraded/get_run_id`.
