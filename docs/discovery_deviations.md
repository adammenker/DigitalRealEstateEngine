# Discovery Completion Deviations

This implementation keeps the discovery process focused on opportunity evidence.

## Intentional Deviations

- No domain, outreach, site generation, deployment, lead routing, or billing features were added.
- Manual SERP classification override fields were added to the model/database, but no override UI was added.
- Local demand estimation remains conservative. It does not use fuzzy national/local market modeling unless population metadata is available.
- Async scans remain an in-process database-backed worker, not an external queue service.
- Geography is still lightweight. A fuller Pelias/local gazetteer setup remains in the production backlog.

## Verification Note

Backend checks pass locally. Direct frontend build cannot run with the host Node 16 runtime; use Docker-backed `make frontend-build` or `make verify`.
