# Anke Sports service

Use the parent workspace agreement when present. This repository owns Firebase authentication, FastAPI on Azure Functions, SQL data, durable jobs, sports/video integrations, ICS and MCP. Reference FormaLM's separation of routes, services and repositories; never copy its secrets or share its production data.

- Keep routes thin. Business rules operate independently of HTTP/queue/MCP transports. Export Pydantic OpenAPI to `contracts/openapi.json` when contracts change.
- Azure MySQL is the production SQL target; SQLite is an explicit local development adapter. Azure Storage Queue triggers dispatch persistent outbox work; local worker executes the same job handlers.
- Preserve stable event IDs/UIDs, personal ownership, link blocks, conditional Feed requests, atomic outbox and retry safety. Test meaningful database and HTTP paths.
- Firebase Admin verifies production identity. Local preview identity is isolated, loopback-only and forbidden in deployed mode; never accept userId as authority.
- Log opaque request/job IDs. Redact tokens and database credentials. Never log private Feed paths. Provider or partial-page errors must retain the prior schedule.
- Inspect this repository's Git and checks independently. Do not deploy or migrate an inferred cloud target. Missing external credentials means unverified integration, not a reason to fake success.
- Keep commands in `README.md`, current state in `STATE.md`, architecture decisions in `docs/`, and sanitized acceptance results in `evidence/`.
