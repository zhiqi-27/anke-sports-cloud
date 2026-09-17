# Anke Sports service

Use the parent workspace agreement when present. This repository owns Firebase authentication, FastAPI on Azure Functions, authoritative data, durable jobs, sports schedule and broadcast-link integrations, ICS and MCP. Video/AI content materials are historical only. Reference FormaLM's separation of routes, services and repositories; never copy its secrets or share its production data.

- Keep routes thin. Business rules operate independently of HTTP/queue/MCP transports. Export Pydantic OpenAPI to `contracts/openapi.json` when contracts change.
- Early-stage priority (2026-09-10, narrowed 2026-09-17): make the existing calendar/follows/personal broadcast-links/ICS path usable and inspectable. Creator/video/AI content is outside 1.0. Defer general GC, speculative capacity work and broad migration completeness; implement only what a concrete current usage or release blocker needs. Do not add infrastructure just because GUI/login work is waiting for the owner.
- The selected cloud target is Azure Cosmos DB for NoSQL Serverless with Periodic backups (user decision 2026-09-10). Existing SQLAlchemy/SQLite/MySQL code is the migration baseline, not a completed Cosmos adapter. Preserve partition-local atomic business changes/outbox, ETag conflicts and immutable published Feed generations; never emulate cross-partition transactions as atomic. Azure Storage Queue and local workers must share verified business handlers.
- Preserve stable event IDs/UIDs, personal ownership, link blocks, conditional Feed requests, atomic outbox and retry safety. Test meaningful database and HTTP paths.
- Firebase Admin verifies production identity. Local preview identity is isolated, loopback-only and forbidden in deployed mode; never accept userId as authority.
- Log opaque request/job IDs. Redact tokens and database credentials. Never log private Feed paths. Provider or partial-page errors must retain the prior schedule.
- Inspect this repository's Git and checks independently. Do not deploy or migrate an inferred cloud target. Missing external credentials means unverified integration, not a reason to fake success.
- Keep commands in `README.md`, current state in `STATE.md`, architecture decisions in `docs/`, and sanitized acceptance results in `evidence/`.

- Both development and production start with Serverless + Periodic. Production does not imply Provisioned or Autoscale. Capacity growth: keep Periodic backups; evaluate measured load and cost before the irreversible in-place Serverless → manual Provisioned conversion, then adjust Autoscale. Update IaC to the resulting capacity mode; do not reapply the initial Serverless template after conversion.
