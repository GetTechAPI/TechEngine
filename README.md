# TechEngine

> **Validation, ingestion, and serving engine for the [TechAPI](https://github.com/GetTechAPI/TechAPI) dataset.**

[![test](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml/badge.svg)](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml)
&nbsp;Code: **MIT** - Data: lives in **[TechAPI](https://github.com/GetTechAPI/TechAPI)** (CC-BY-SA 4.0)

TechEngine owns everything *around* the data: schema validation, the FastAPI
read API, the static JSON dump generator, the engine's own landing site,
automated coverage checks, and the weekly ingestion/refresh crawlers.

The dataset and the public-facing playground site live in
[TechAPI](https://github.com/GetTechAPI/TechAPI) so each can be versioned,
mirrored, and licensed independently. The site shipped in this repo is the
engine's own landing: what TechEngine is, what it runs, and links out to docs.

## Layout

```text
app/
  validate.py          # schema/range/uniqueness checks
  seed.py              # data/ -> SQLModel database
  dump.py              # API -> static JSON tree
  main.py              # FastAPI entrypoint
  models/              # SQLModel tables
  routers/             # /v1/{brands,socs,smartphones,gpus,cpus}
  schemas/             # Pydantic response models
  services/            # scoring (algorithm_version-tagged)
  coverage/            # upstream-vs-curated diff + Markdown report
  ingest/              # draft new records from upstream pages
tests/                 # unit + integration
site/                  # Astro engine landing (deploys to Pages)
docs/                  # SPEC / DATA_PIPELINE / DEVELOPMENT
TechAPI/               # submodule -> GetTechAPI/TechAPI (clickable @ <sha> link)
.github/workflows/
  test.yml             # lint + type-check + tests
  validate-data.yml    # read-only data validation (workflow_call or manual)
  deploy-pages.yml     # build & deploy engine site + dump
  coverage-report.yml  # manual: gap report to the job summary + artifact
  ingest.yml           # workflow_call: draft new SKUs -> one PR on TechAPI
  refresh.yml          # workflow_call: re-scrape benchmarks -> one PR on TechAPI
  verify-promote.yml   # workflow_call: Tier 1-3 checks + verified promotion PR
```

## How The Two Repos Connect

Both repos live in the **GetTechAPI** org and each includes the other as a git
**submodule** (a clickable `@ <sha>` pin). Nothing syncs them automatically.

No workflow here runs on a schedule, posts comments, or uses a personal or
bot-account token (ADR-017). Collection runs only when a maintainer dispatches
TechAPI's `collect.yml`, which calls `ingest.yml`, `refresh.yml` or
`verify-promote.yml` with TechAPI's own `GITHUB_TOKEN`. Each task keeps at most
one open PR on a fixed `collect/*` branch, updated in place, after
`app.validate` and `integrity_check.py --strict` pass. The engine that runs is
the TechEngine commit TechAPI pins as its submodule. Crawlers identify
themselves with a `TechEngine-*` User-Agent.

Every Python entry point reads data from a **TechAPI checkout**. The location
can be overridden via `TECHAPI_DATA_DIR`; by default TechEngine supports both
layouts: `TechAPI/TechEngine` as a submodule and `TechAPI` beside `TechEngine`.

## Quickstart

```bash
git clone https://github.com/GetTechAPI/TechAPI.git ../TechAPI   # data source
pip install -e ".[dev]"
python -m app.validate          # check data integrity
python -m app.seed              # data/ -> ./techapi.db (SQLite)
uvicorn app.main:app --reload   # serve; curl localhost:8000/v1/cpus/ryzen-9-9950x3d
python -m app.dump              # generate ./dump/v1/... static tree
```

`TECHAPI_DATA_DIR=/path/to/TechAPI/data` overrides the data location.

For tablets, watches, and PDAs, `battery_mah` and `weight_g` are optional:
seed records may omit them or use `null` when no source value is known, and API
detail responses and static dumps preserve unknown values as `null`.
Smartphones continue to require numeric values for both fields.

### Docker Compose (Postgres)

```bash
docker compose up --build
```

Spins up Postgres 16, seeds from the mounted TechAPI checkout, serves on `:8000`.

## Roadmap

- [x] Split out from TechAPI; sibling-checkout data pipeline
- [x] **Coverage gap detector** - diff curated dataset vs upstream catalogs
  and surface missing SKUs as a sticky weekly issue
  ([#1](https://github.com/GetTechAPI/TechEngine/issues/1))
- [x] **Ingestion crawler** - scrape canonical sources and draft new SKUs for
  a TechAPI PR ([#2](https://github.com/GetTechAPI/TechEngine/issues/2));
  manual dispatch since ADR-017 (`ingest.yml`)
- [x] **Refresh pipeline** - live benchmark enrichment -> full-dataset
  integrity gate -> TechAPI PR; manual dispatch since ADR-017 (`refresh.yml`)
- [ ] More sources (Intel ARK, AMD product pages, TechPowerUp DB)

## License

Code is licensed under the [MIT License](LICENSE). The dataset (in TechAPI) is
licensed CC-BY-SA 4.0 separately.
