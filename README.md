# TechEngine

> **Validation, ingestion, and serving engine for the [TechAPI](https://github.com/GetTechAPI/TechAPI) dataset.**

[![test](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml/badge.svg)](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml)
&nbsp;Code: **MIT** · Data: lives in **[TechAPI](https://github.com/GetTechAPI/TechAPI)** (CC-BY-SA 4.0)

TechEngine owns everything *around* the data: schema validation, the FastAPI
read API, the static JSON dump generator, the engine's own landing site, and
(next up) automated coverage checks and a weekly ingestion crawler.

The dataset and the public-facing playground site live in
[TechAPI](https://github.com/GetTechAPI/TechAPI) so each can be versioned,
mirrored, and licensed independently. The site shipped in this repo is the
engine's own landing — what TechEngine is, what it runs, link out to docs.

## Layout

```
app/
  ├ validate.py        # schema/range/uniqueness checks
  ├ seed.py            # data/ → SQLModel database
  ├ dump.py            # API → static JSON tree
  ├ main.py            # FastAPI entrypoint
  ├ models/            # SQLModel tables
  ├ routers/           # /v1/{brands,socs,smartphones,gpus,cpus}
  ├ schemas/           # Pydantic response models
  ├ services/          # scoring (algorithm_version-tagged)
  ├ coverage/          # upstream-vs-curated diff + Markdown report
  └ ingest/            # draft new records from upstream pages
tests/                 # unit + integration
site/                  # Astro engine landing (deploys to Pages)
docs/                  # SPEC / DATA_PIPELINE / DEVELOPMENT
TechAPI/               # submodule → GetTechAPI/TechAPI (clickable @ <sha> link)
.github/workflows/
  ├ validate-data.yml   # workflow_call: PR-time data validation for TechAPI
  ├ weekly-refresh.yml  # cron: live-scrape → integrity gate → dump → PR to TechAPI
  ├ weekly-ingest.yml   # cron: draft new SKUs, open PR against TechAPI
  ├ coverage-report.yml # cron: gap report, sticky issue (TechEngine + TechAPI)
  ├ refresh-data.yml    # smoke-test: rebuild the dump on engine (app/**) changes
  ├ notify-techapi.yml  # push→main: ping TechAPI to bump its TechEngine submodule
  ├ bump-techapi.yml    # dispatch: advance this repo's TechAPI submodule pointer
  ├ deploy-pages.yml    # build & deploy engine site + dump
  └ test.yml            # lint + type-check + tests
```

## How the two repos connect

Both repos live in the **GetTechAPI** org and each includes the other as a git
**submodule** (a clickable `@ <sha>` pin). Three automations keep them in step:

- **validate-data.yml** (`workflow_call`) — TechAPI's PR-time check calls into
  TechEngine to validate its data.
- **weekly-refresh.yml** — live-scrapes benchmarks, runs the full-dataset
  integrity gate (`app.validate` + `integrity_check.py --strict`), regenerates
  the static dump, and opens a dated refresh PR against TechAPI.
- **Submodule autosync** — every push to TechEngine `main` fires
  `notify-techapi.yml`, which pings TechAPI to bump its TechEngine pointer;
  conversely `bump-techapi.yml` advances TechEngine's TechAPI pointer when
  TechAPI changes. Bumps are loop-guarded, so each real change converges to one.

Every Python entry point reads data from a **TechAPI checkout**. The location
can be overridden via `TECHAPI_DATA_DIR`; the default looks for `../TechAPI/data`
next to this repo, which matches a local dev layout.

## Quickstart

```bash
git clone https://github.com/GetTechAPI/TechAPI.git ../TechAPI   # data source
pip install -e ".[dev]"
python -m app.validate          # check data integrity
python -m app.seed              # data/ → ./techapi.db (SQLite)
uvicorn app.main:app --reload   # serve; curl localhost:8000/v1/cpus/ryzen-9-9950x3d
python -m app.dump              # generate ./dump/v1/... static tree
```

`TECHAPI_DATA_DIR=/path/to/TechAPI/data` overrides the data location.

### Docker Compose (Postgres)

```bash
docker compose up --build
```

Spins up Postgres 16, seeds from the mounted TechAPI checkout, serves on `:8000`.

## Roadmap

- [x] Split out from TechAPI; sibling-checkout data pipeline
- [x] **Coverage gap detector** — diff curated dataset vs upstream catalogs
  and surface missing SKUs as a sticky weekly issue
  ([#1](https://github.com/GetTechAPI/TechEngine/issues/1))
- [x] **Weekly ingestion crawler** — scrape canonical sources and open PRs
  against TechAPI with new SKUs (requires the `TECHAPI_TOKEN` secret to push)
  ([#2](https://github.com/GetTechAPI/TechEngine/issues/2))
- [x] **Weekly refresh pipeline** — live benchmark enrichment → full-dataset
  integrity gate → static dump → dated refresh PR (`weekly-refresh.yml`)
- [x] **Bidirectional submodule autosync** between TechEngine and TechAPI
- [ ] More sources (Intel ARK, AMD product pages, TechPowerUp DB)

## License

Code is licensed under the [MIT License](LICENSE). The dataset (in TechAPI) is
licensed CC-BY-SA 4.0 separately.
