# Digital Real Estate Engine

Local-first platform for finding and evaluating rank-and-rent local lead generation opportunities.

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
rank-rent init-db
rank-rent qualify --fixtures
rank-rent scan --service water_heater_services --market lower_fairfield_county
rank-rent site generate 1
rank-rent site preview 1
rank-rent web
```

## Run the engine UI with Docker

```bash
docker compose up -d --build
```

Open the Next.js engine dashboard at [http://127.0.0.1:8010](http://127.0.0.1:8010).

The Compose service uses `restart: unless-stopped`, so it will keep running and restart when Docker
Desktop starts again. SQLite data is stored in the `rank_rent_data` Docker volume, and generated
sample sites are written to `./generated_sites`.

To use a different host port:

```bash
RANK_RENT_PORT=8000 docker compose up -d --build
```

The Python backend is also exposed for debugging at
[http://127.0.0.1:8011](http://127.0.0.1:8011). The frontend talks to it through an internal
Compose network.

Useful commands:

```bash
docker compose ps
docker compose logs -f
docker compose restart
docker compose down
```

Schema changes are applied through Alembic when the backend starts. For a clean local testing DB,
run `rank-rent reset-db --confirm` inside the backend environment or `docker compose down -v`.
Recorded replay bundles can be checked with `rank-rent fixtures validate <bundle_path>`.

The default path uses deterministic fixtures and mock providers. When `DATA_MODE=live` is enabled,
DataForSEO targets `DATAFORSEO_ENVIRONMENT=sandbox` by default, which uses
`https://sandbox.dataforseo.com/v3/...` and returns free dummy responses with production-shaped
payloads. Paid production calls require explicitly setting `DATAFORSEO_ENVIRONMENT=production`,
`ALLOW_LIVE_API_CALLS=true`, and credentials.

This project does not claim an opportunity is guaranteed to rank or become profitable. Scores are
deterministic research aids with explicit assumptions and missing-data flags.
