# TechEngine development guide

> Read this first. Full spec: [SPEC.md](SPEC.md). Static-dump design:
> [DATA_PIPELINE.md](DATA_PIPELINE.md).

## What this is

TechEngine is the engine for the TechAPI dataset of consumer-electronics specs:
the validator, the FastAPI read API, the static JSON dump generator, the
coverage report, the collectors (`app/ingest`) and the verification tiers
(`app/verify`). **It holds no data.** The dataset lives in
[GetTechAPI/TechAPI](https://github.com/GetTechAPI/TechAPI) and is read from a
TechAPI checkout (see the note under "Repo layout").

## Current status

- **Stack**: FastAPI, SQLModel, Pydantic v2, Python 3.12. DB defaults to SQLite
  (`DATABASE_URL`); Postgres 16 via `docker-compose.yml`.
- **Endpoints** (under `/v1`): `health`, `version`, and one list + `/{slug}`
  detail per collection: `brands` (+`/{slug}/smartphones`), `socs`
  (+`/{slug}/smartphones`), `smartphones`, `tablets`, `watches`, `pdas`, `gpus`,
  `cpus` (`?segment=`), `laptops`, `monitors`, `software`, `websites`,
  `device-catalog`. `socs`, `smartphones`, `gpus` and `cpus` also have
  `/{slug}/score`. Docs at `/scalar` and `/docs`.
- **Formats**: list `{count,next,previous,results}` (§7.4); error
  `{error:{code,message,request_id,documentation_url}}` (§7.5); smartphone detail
  per SPEC appendix C (embeds brand+soc+manufacturer + computed `score`).
- **Tests**: `pytest` is the canonical test runner. `ruff check app tests` +
  `mypy app` are the static gates in CI.
- **Hosting**: there is no hosted API server. `deploy-pages.yml` publishes the
  landing site with a static dump to GitHub Pages on every push to `main`;
  TechAPI builds its own copy of the dump with the engine commit it pins.

### Data scope

The dataset is large and changes often, so no record counts are kept here; see
TechAPI's README. Collectors live in `app/ingest` and `app/verify`. Nothing runs
on a schedule: they run when a maintainer dispatches TechAPI's `collect.yml`,
and each run uploads a patch that a maintainer turns into a TechAPI PR. Do not
run them against a TechAPI checkout you are not prepared to diff and review.

## Repo layout

```
app/                 FastAPI app
  main.py            entrypoint (CORS, request-id mw, error handlers, Scalar, routers)
  config.py          pydantic-settings (DATABASE_URL, etc.)
  database.py        engine + get_session (SQLite or Postgres)
  models/            SQLModel tables: brand, soc, smartphone, gpu, cpu
  schemas/           Pydantic response models + serializers.py (ORM→schema, URLs)
  services/scoring/  open 0–100 scoring (§8); weights in config/scoring.yaml
  routers/           meta, brands, socs, smartphones, mobile_devices (tablets,
                     watches, pdas), gpus, cpus, laptops, monitors, software,
                     websites, device_catalog
  errors.py          §7.5 error envelope + handlers
  data_root.py       finds the dataset: TECHAPI_DATA_DIR, ../data, ../TechAPI/data
  seed.py            dataset → DB (recurses brand subfolders; coerces ISO dates)
  validate.py        schema/range/FK/slug checks
  dump.py            DB → static JSON dump (replays API in-process) → ./dump
  coverage/          upstream-vs-curated gap report
  ingest/            collectors: draft new records, refresh benchmarks
  verify/            trust tiers 0–3, verified promotion, backfill tools
integrity_check.py   whole-dataset anomaly scan (the --strict gate)
config/scoring.yaml  scoring weights and reference scales
tests/               unit/ + integration/ + verify/
site/                Astro landing page (deploy-pages.yml publishes it with the dump)
docs/                SPEC.md, DATA_PIPELINE.md, DEVELOPMENT.md
.github/workflows/   test.yml, validate-data.yml, deploy-pages.yml, coverage-report.yml,
                     final-check.yml (`final-check` label -> one TechEngineBot comment), and
                     the manual change generators TechAPI's collect.yml calls: ingest.yml,
                     refresh.yml, verify-promote.yml (read-only, each uploads
                     collect.patch; nothing scheduled; ADR-017)
```

> Note: the dataset is **not in this repo**. It lives in TechAPI's `data/`
> (`brand/<country>/<slug>.json`, `cpu/<manufacturer>/<year>/<segment>/<slug>.json`, …)
> and is found through `app/data_root.py`. Data folders are singular
> (`data/soc/…`) but API routes are plural (`/v1/socs`). Intentional.

## How to run

```bash
git clone https://github.com/GetTechAPI/TechAPI.git ../TechAPI   # the data
pip install -e ".[dev]"
python -m app.validate           # check the dataset
python -m app.seed               # dataset → ./techapi.db (SQLite)
uvicorn app.main:app --reload    # serve; curl http://localhost:8000/v1/smartphones/galaxy-s25
pytest -q --cov=app              # tests (CI fails under 60% coverage)
ruff check app tests && mypy app
python -m app.dump               # generate ./dump/ static tree (gitignored)
```

## Key decisions / deviations from the original SPEC

- **CPU category added** (not in original §6) — `app/models/cpu.py`, `/v1/cpus`,
  recorded as **ADR-011** / **§6.7** in docs/SPEC.md (maintainer wants computer chips).
- **GPU activated** — model existed (§6.5); endpoints + data added.
- **Data restructured** to singular names + brand subfolders (maintainer request).
- **Static-dump pivot** — `app/dump.py` exports the API to a static JSON tree,
  built by TechAPI at deploy time (see DATA_PIPELINE.md §3).
- **Scoring** reads its weights and reference scales from `config/scoring.yaml`
  (§8). Raw third-party benchmarks (Geekbench/AnTuTu/Cinebench/Time Spy) are
  stored as algorithm inputs but NOT exposed (ADR-006).

## Conventions (IMPORTANT)

- **Commits**: Conventional Commits (`feat(api): …`, `fix(ingest): …`, `docs: …`).
  Keep commit messages clean and to the point.
- **Data accuracy**: only real, sourced models. All brand, device, and chip
  records require `source_urls`. Do not fabricate unsourced "all devices" data
  (SPEC §1.6).
- 100% type hints; tests under `tests/`; new top-level data fields must match the
  SQLModel (seeder does `Model(**record)` — unknown keys break it).

## Gotchas

- SQLModel `table=True` models skip Pydantic validation, so `seed.py` coerces
  `release_date` strings to `date` objects manually.
- The camera score in `app/services/scoring/phones.py` is intentionally simple
  (a 50MP flagship can score below a 200MP phone): a known limitation, not a
  data bug.
- `app.seed` reads record timestamps from the TechAPI checkout's git history; a
  shallow clone or a plain copy of `data/` falls back to the seed time.
