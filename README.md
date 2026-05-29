# TechEngine

> **Validation, ingestion, and serving engine for the [TechAPI](https://github.com/GetTechAPI/TechAPI) dataset.**

[![test](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml/badge.svg)](https://github.com/GetTechAPI/TechEngine/actions/workflows/test.yml)
&nbsp;Code: **MIT** · Data: lives in **[TechAPI](https://github.com/GetTechAPI/TechAPI)** (CC-BY-SA 4.0)

TechEngine owns everything *around* the data: schema validation, the FastAPI
read API, the PokeAPI-style static JSON dump, the Astro intro/playground site,
and (next up) automated coverage checks and a weekly ingestion crawler.

The data itself stays in [TechAPI](https://github.com/GetTechAPI/TechAPI) so it
can be versioned, mirrored, and licensed independently.

## Layout

```
app/
  ├ validate.py        # schema/range/uniqueness checks
  ├ seed.py            # data/ → SQLModel database
  ├ dump.py            # API → static JSON tree (PokeAPI-style)
  ├ main.py            # FastAPI entrypoint
  ├ models/            # SQLModel tables
  ├ routers/           # /v1/{brands,socs,smartphones,gpus,cpus}
  ├ schemas/           # Pydantic response models
  └ services/          # scoring (algorithm_version-tagged)
tests/                 # unit + integration
site/                  # Astro homepage + playground (deployed to Pages)
docs/                  # SPEC / DATA_PIPELINE / DEVELOPMENT
.github/workflows/
  ├ validate-data.yml  # workflow_call: PR-time data validation for TechAPI
  ├ refresh-data.yml   # cron: regenerate the static dump weekly
  ├ deploy-pages.yml   # build & deploy Astro site + dump
  └ test.yml           # lint + type-check + tests
```

## How the two repos connect

```
┌────────────────────┐                ┌──────────────────────────┐
│ TechAPI (data/)    │  workflow_call │ TechEngine (this repo)   │
│ + bundled self-    │ ─────────────▶ │  validate-data.yml       │
│   check (PR)       │ ◀───────────── │  (checks out TechAPI)    │
└────────────────────┘                └──────────────────────────┘
```

Every Python entry point reads data from a sibling **TechAPI checkout**. The
location can be overridden via `TECHAPI_DATA_DIR`; the default looks for
`../TechAPI/data` next to this repo, which matches a local dev layout.

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
- [ ] **Coverage gap detector** — diff curated dataset vs upstream catalogs
  (Intel ARK, AMD product pages, Wikipedia infoboxes, TechPowerUp DB) and emit
  weekly issues listing missing SKUs ([#1](https://github.com/GetTechAPI/TechEngine/issues/1))
- [ ] **Weekly ingestion crawler** — scrape canonical sources and open PRs
  against TechAPI with new SKUs ([#2](https://github.com/GetTechAPI/TechEngine/issues/2))

## License

Code is licensed under the [MIT License](LICENSE). The dataset (in TechAPI) is
licensed CC-BY-SA 4.0 separately.
