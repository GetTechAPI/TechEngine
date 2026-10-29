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
  validate-data.yml                  # workflow_call: PR-time data validation for TechAPI
  techapi-pr-validation-comment.yml  # workflow_call: TechAPI PR review comment
  techapi-verify-comment.yml         # workflow_call: Tier 0 / `/verify` report on a TechAPI PR
  weekly-refresh.yml                 # workflow_call: live-scrape -> integrity gate -> dump -> PR
  weekly-ingest.yml                  # workflow_call: draft new SKUs, open PR against TechAPI
  verify-network.yml                 # workflow_call: Tier 1-3 checks + verified promotion PR
  coverage-report.yml                # cron (weekly): gap report, sticky issue in this repo
  refresh-data.yml                   # smoke-test: rebuild the dump on engine (app/**) changes
  deploy-pages.yml                   # build & deploy engine site + dump
  test.yml                           # lint + type-check + tests
```

## How The Two Repos Connect

Both repos live in the **GetTechAPI** org and each includes the other as a git
**submodule** (a clickable `@ <sha>` pin). Pointers are bumped by hand; nothing
syncs them automatically.

Every automation that touches TechAPI runs **in TechAPI**, which calls the
TechEngine workflows above as reusable workflows (`workflow_call`, `@main`).
A called workflow gets the caller's `GITHUB_TOKEN`, so branches, PRs and
comments are made by `github-actions[bot]`. No personal access token or bot
account is used anywhere (ADR-017):

- **validate-data.yml / techapi-pr-validation-comment.yml /
  techapi-verify-comment.yml** - TechAPI PRs are validated and reviewed.
- **weekly-refresh.yml / weekly-ingest.yml / verify-network.yml** - TechAPI's
  `weekly-data.yml` runs them and they open PRs against TechAPI `develop`.

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
- [x] **Weekly ingestion crawler** - scrape canonical sources and open PRs
  against TechAPI with new SKUs
  ([#2](https://github.com/GetTechAPI/TechEngine/issues/2))
- [x] **Weekly refresh pipeline** - live benchmark enrichment -> full-dataset
  integrity gate -> static dump -> dated refresh PR (`weekly-refresh.yml`)
- [x] **Bidirectional submodule autosync** between TechEngine and TechAPI
- [ ] More sources (Intel ARK, AMD product pages, TechPowerUp DB)

## License

Code is licensed under the [MIT License](LICENSE). The dataset (in TechAPI) is
licensed CC-BY-SA 4.0 separately.
