# TechEngine

> **Validation, ingestion, and serving engine for the [TechAPI](https://github.com/GetTechAPI/TechAPI) dataset.**

[![test](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml/badge.svg)](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml)
&nbsp;Code: **MIT** - Data: lives in **[TechAPI](https://github.com/GetTechAPI/TechAPI)** (CC-BY-SA 4.0)

TechEngine owns everything *around* the data: schema validation, the FastAPI
read API, the static JSON dump generator, the engine's own landing site,
coverage checks, the ingestion/refresh collectors and the verification tiers.
Nothing here runs on a schedule; collectors run only when a maintainer
dispatches them.

The dataset and the public-facing playground site live in
[TechAPI](https://github.com/GetTechAPI/TechAPI) so each can be versioned,
mirrored, and licensed independently. The site shipped in this repo is the
engine's own landing: what TechEngine is, what it runs, and links out to docs.

## Layout

```text
app/                   # the engine (the one installable Python package)
  main.py              # FastAPI entry point; mounts every router under /v1
  routers/             # /v1 routes, one module per collection (list below)
  models/              # SQLModel tables
  schemas/             # Pydantic response models + serializers
  services/scoring/    # 0-100 scores, weights from config/scoring.yaml
  data_root.py         # finds the TechAPI dataset (see below)
  categories.py        # the 13 dataset categories and their collection names
  validate.py          # schema/range/uniqueness checks on the dataset
  seed.py              # dataset -> SQL database
  dump.py              # API -> static JSON tree (./dump)
  coverage/            # upstream-vs-curated gap report
  ingest/              # collectors: draft new records, refresh benchmarks
  verify/              # trust tiers 0-3, verified promotion, backfill tools
config/scoring.yaml    # scoring weights and reference scales
integrity_check.py     # whole-dataset anomaly scan (the --strict gate)
tests/                 # unit/, integration/, verify/
site/                  # Astro landing page, deployed to GitHub Pages
docs/                  # SPEC, DATA_PIPELINE, DEVELOPMENT
.github/workflows/     # CI; every workflow is listed below
Dockerfile, docker-compose.yml   # API + Postgres, dataset mounted (see Docker)
```

Routes under `/v1`: `health`, `version`, `brands`, `socs`, `smartphones`,
`tablets`, `watches`, `pdas`, `gpus`, `cpus`, `laptops`, `monitors`,
`software`, `websites`, `device-catalog`. Dataset folders are singular
(`cpu/`), routes are plural (`/v1/cpus`); `app/categories.py` holds the mapping.

**How the engine finds the dataset.** This repo holds no data. Every entry
point asks `app/data_root.py`, which returns the first of: the
`TECHAPI_DATA_DIR` environment variable; `../data` (this repo checked out as
TechAPI's `TechEngine` submodule); `../TechAPI/data` (the two repos cloned side
by side). CI checks TechAPI out into `./TechAPI`, which is git-ignored, and
sets `TECHAPI_DATA_DIR` to its `data/` folder.

Workflows:

```text
test.yml             # lint + type-check + tests
validate-data.yml    # read-only data validation (workflow_call or manual)
deploy-pages.yml     # on push to main: build site/ + static dump -> Pages
coverage-report.yml  # manual: gap report to the job summary + artifact
ingest.yml           # workflow_call: draft new SKUs -> collect.patch
refresh.yml          # workflow_call: re-scrape benchmarks -> collect.patch
verify-promote.yml   # workflow_call: Tier 1-3 checks + verified promotion patch
final-check.yml      # label-triggered checks + one TechEngineBot comment
```

## How The Two Repos Connect

Both repos live in the **GetTechAPI** org. TechAPI pins this repo as its
`TechEngine` git **submodule** (a clickable `@ <sha>` pin); this repo does not
pin TechAPI back, so there is no submodule loop. Nothing syncs them
automatically: the pin moves only when a TechAPI commit moves it.

No workflow here runs on a schedule or uses a personal access token (ADR-017),
and no workflow opens or approves a PR. TechEngineBot opens PRs by hand from a
maintainer's machine, with its gh login kept apart from the maintainer's own;
the maintainer's account still commits, pushes and merges. The one comment any
workflow posts is TechEngineBot's final check, and only when a maintainer adds
the `final-check` label to a PR (`final-check.yml`).
Collection runs only when a maintainer dispatches TechAPI's `collect.yml`, which
calls `ingest.yml`, `refresh.yml` or `verify-promote.yml` with a read-only
token. Each run commits locally, gates the result with `app.validate` and
`integrity_check.py --strict`, and uploads the commit as `collect.patch`. A
maintainer then runs TechAPI's `.github/scripts/open-collect-pr.sh` by hand:
their account pushes the run's `collect/*` branch and TechEngineBot opens the
PR. No workflow pushes or opens a PR. The
engine that runs is the TechEngine commit TechAPI pins as its submodule.
Crawlers identify themselves with a `TechEngine-*` User-Agent.

Every Python entry point reads data from a **TechAPI checkout**. The location
can be overridden via `TECHAPI_DATA_DIR`; by default TechEngine supports both
layouts: `TechAPI/TechEngine` as TechAPI's submodule and `TechAPI` beside
`TechEngine` (see Layout).

## Quickstart

```bash
git clone https://github.com/GetTechAPI/TechAPI.git ../TechAPI   # data source
pip install -e ".[dev]"
python -m app.validate          # check data integrity
python -m app.seed              # dataset -> ./techapi.db (SQLite)
uvicorn app.main:app --reload   # serve; curl localhost:8000/v1/cpus/ryzen-9-9950x3d
python -m app.dump              # generate ./dump/v1/... static tree
```

`TECHAPI_DATA_DIR=/path/to/TechAPI/data` overrides the data location.

For tablets, watches, and PDAs, `battery_mah` and `weight_g` are optional:
seed records may omit them or use `null` when no source value is known, and API
detail responses and static dumps preserve unknown values as `null`.
Smartphones continue to require numeric values for both fields.

### Docker

The image holds the engine only and expects the dataset at `/data`
(`TECHAPI_DATA_DIR`). `docker-compose.yml` starts Postgres and the API, and
mounts `../TechAPI/data` read-only at that path, so clone the two repos side by
side:

```text
<parent>/TechEngine   # this repo; run `docker compose up --build` here
<parent>/TechAPI      # the dataset
```

The API seeds the database from the mount before it serves, which takes a
while on the full dataset. The image has no git, so record timestamps are the
seed time. No workflow builds or uses these files.

## Roadmap

- [x] Split out from TechAPI; sibling-checkout data pipeline
- [x] **Coverage gap detector** - diff curated dataset vs upstream catalogs
  and list missing SKUs in an on-demand report (`coverage-report.yml`)
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
