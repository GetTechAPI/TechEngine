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
```

## How The Two Repos Connect

Both repos live in the **GetTechAPI** org and each includes the other as a git
**submodule** (a clickable `@ <sha>` pin). Nothing syncs them automatically.

No workflow here runs on a schedule, writes to TechAPI, or uses a personal or
bot-account token (ADR-017). Crawlers (`app.ingest`, `app.ingest.enrich`,
`app.verify`) run only when a maintainer starts them locally; they identify
themselves with a `TechEngine-*` User-Agent and their output reaches TechAPI as
an ordinary reviewed PR.

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
  a TechAPI PR ([#2](https://github.com/GetTechAPI/TechEngine/issues/2)); run
  by hand since ADR-017
- [x] **Refresh pipeline** - live benchmark enrichment -> full-dataset
  integrity gate -> static dump; run by hand since ADR-017
- [ ] More sources (Intel ARK, AMD product pages, TechPowerUp DB)

## License

Code is licensed under the [MIT License](LICENSE). The dataset (in TechAPI) is
licensed CC-BY-SA 4.0 separately.
