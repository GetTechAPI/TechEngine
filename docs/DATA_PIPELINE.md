# Data Pipeline: Static Dump, Automation, And Collection

This document explains how TechAPI moves from a database to a **static JSON
dataset**, how that dataset is **refreshed automatically**, how data is
**collected** from open sources, and the **public-vs-private** options for the
data itself. It complements SPEC Section 4.2 (data flow), Section 9 (collection), Section 16
(CI/CD), and ADR-003/005.

## 1. Why Static?

Device specs change on a **release cadence**, not in real time; a weekly (or
daily) refresh is plenty. That makes a pre-generated static dataset ideal:

- **Cheap & reliable**: served as plain files from a CDN / GitHub Pages / raw
  URLs; no always-on server or database to operate or pay for.
- **Versioned**: every refresh is a Git commit, so the history *is* a free
  time-series of spec changes ("git scraping").
- **Fast**: edge-cached files, no cold starts.

### How Others Do It

- **Git scraping** (Simon Willison): a scheduled GitHub Actions job fetches
  data, pretty-prints it, and commits it back when it changes; no server
  needed. See [simonwillison.net](https://simonwillison.net/2020/Oct/9/git-scraping/).

TechAPI already anticipated this: SPEC Section 4.2 (DB -> static dump), Section 5.2
(`api-data` repo), Section 16.1 (`dump-data.yml`).

## 2. The Dump Generator (`app/dump.py`)

`python -m app.dump` seeds a database, then **replays the real API
endpoints through an in-process client** and writes each response to disk. The
static files therefore byte-match the live API (no serialization drift).

Output layout (under `dump/`):

```text
dump/v1/index.json                              # manifest: collections + counts
dump/v1/smartphones/index.json                  # { count, results: [all refs] }
dump/v1/smartphones/galaxy-s25/index.json       # full detail (== GET /v1/smartphones/galaxy-s25)
dump/v1/smartphones/galaxy-s25/score/index.json # score sidecar
dump/v1/socs/... /v1/gpus/... /v1/cpus/... /v1/brands/...
```

A static consumer just fetches, e.g.
`https://<host>/v1/smartphones/galaxy-s25/index.json`.

## 3. Automated Refresh (`.github/workflows/weekly-refresh.yml`)

The weekly pipeline (Monday cron + manual dispatch) runs the full cycle against
a TechAPI checkout:

```text
live-scrape benchmark sources -> full-dataset integrity gate
  (app.validate + integrity_check.py --strict) -> regenerate static dump
  -> open a dated refresh PR against TechAPI
```

The integrity gate re-checks the **whole** dataset every run (not just new
rows), so a bad scrape cannot slip a contaminated value past it. A lighter
`refresh-data.yml` rebuilds the dump on engine (`app/**`) changes as a smoke
test only. This is the git-scraping pattern: GitHub runs and stores everything
for free, and the dated PR keeps every refresh reviewable before it lands. The
hosting target depends on the public/private decision (Section 5).

## 4. Where The Data Comes From

This repo serves **curated, validated** records, but collection now happens
**in-repo**: `app/ingest` drafts new SKUs from upstream catalogs and
`app/ingest/enrich` backfills benchmark columns from multiple sources
(variant-safe, fill-only-nulls, never overwrites). Both run weekly and open PRs
against TechAPI for human review before anything lands (SPEC Section 9.3). The curated
dataset is a **subset, not exhaustive.**

**Dataset layout (this repo).** Curated data uses singular folder names and is
organised by brand: `data/brand/<country>/<slug>.json`,
`data/soc/<manufacturer>/<year>/<slug>.json`,
`data/smartphone/<brand>/<year>/<slug>.json`,
`data/gpu/<manufacturer>/<year>/<segment>/<slug>.json`, and
`data/cpu/<manufacturer>/<year>/<segment>/<slug>.json`. API routes stay plural,
e.g. `/v1/socs`.

## 5. Public Vs Private Data

The current SPEC positions data as **open** (CC-BY-SA 4.0, ADR-005); that is
core to the "open data platform" identity (Section 1.6). Keeping data private is a
real option but is a **strategy change**, so here are the paths:

| Option | What it means | Trade-offs |
|---|---|---|
| **A. Public (current SPEC)** | `data/` + static dump public, CC-BY-SA | Simplest; matches open-data mission; community can contribute and self-host. Anyone can copy the data. |
| **B. Private factory -> public product** | Scrapers + raw/messy data in a **private** repo; only a curated, licensed **public dump** is published | Best of both: keep collection methods/raw data private, still ship an open API. Two repos to run. |
| **C. Fully private** | Data + dump + API all private (private repo, auth-gated API) | Maximum control / proprietary value, but abandons the open-data positioning, CC-BY-SA, and community contributions. The API stays code-MIT but data is closed. |

**Recommendation:** **B** if the concern is protecting *collection effort /
raw scrape data* while still offering a public API; **C** only if the dataset
itself is meant to be a proprietary asset (then revisit ADR-005, Section 1.6, Section 10 and
the token model in Section 7.6). Either way the **code stays MIT**; what changes is
where `data/` lives and whether the dump is published publicly.

Mechanically, private collection is easy: put `data/` and the collector in a
**private** GitHub repo and run the same Actions there. For option B, that
private repo publishes the curated dump to a separate public repo or Pages on
each refresh.
