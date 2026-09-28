# AMD CPU ingest audit

The reusable CPU table parser now handles stacked CPU headers, separate family
and model columns, rowspan carry, and SKU rows made entirely of `th` cells.
It reads units from headers, excludes GPU/NPU columns, retains variant suffixes,
and does not infer threads from a core count. Architecture comes from the source
column or section, never the product family. Opteron is registered in the existing
CPU collector and therefore uses the normal ingest pipeline and weekly workflow.

CPU additions are deduplicated symmetrically against both names and slugs in the
requested TechAPI data root, ignoring punctuation and manufacturer prefixes.
PRO placement is normalized while PRO/non-PRO and H/HS/U/X/HE variants stay
distinct. Records cite their exact Wikipedia page and stay `verified: false`.

## Dry run against TechAPI develop

Dataset revision: `bf4a0597381cdf208c1fdeceedc1559f2b770bb7` (1,479 AMD CPU
records). The artifact records the dataset content hash and source HTML hashes.

| Live tables | Unique models | Ready additions | Already curated | Incomplete |
| --- | ---: | ---: | ---: | ---: |
| Ryzen | 445 | 52 | 383 | 10 |
| Opteron | 287 | 0 | 102 | 185 |
| Total | 732 | 52 | 485 | 195 |

All 185 unresolved Opteron models lack a stated thread count; 76 also lack a
per-row core count. Across both pages, 22 unresolved models lack a usable release
date. These reasons overlap. Missing table specifications are not evidence that
no public specification exists elsewhere; other authoritative sources can fill
them later without guessing.

The dry-run JSON lists all 52 complete records with their intended output paths,
all incomplete models with reasons, and every already represented model.
No TechAPI files were written and no TechAPI data PR was opened. The existing
weekly workflow creates data PRs against `develop`, but it cannot select just
these two pages and this branch's collector is not deployed on `main`; dispatching
it would also ingest unrelated CPU pages. This is the requested dry-run fallback.

## Reconciliation of issue #19's 759 gaps

The live coverage scraper reproduces **759** exact-slug misses and exactly the
30 rows shown in issue #19: Ryzen contributes 277 entries, Opteron 283, and EPYC
199. Thus the original 759 includes a third page beyond the two requested pages.

| Reproduced gap status | Entries |
| --- | ---: |
| Ready to fill through normal ingest | 21 |
| Already curated under a complete branded name | 324 |
| Missing required table specifications | 167 |
| Family tiers, stepping captions, dates, or part-number cells | 48 |
| EPYC entries outside this two-page audit | 199 |
| Total | 759 |

Of the 759, **21 can be filled by the proposed additions; 738 are not new
additions from this audit**, partitioned above. The 167 incomplete gap entries
include 161 with missing threads, 52 with missing cores, and 17 with missing dates
(overlapping reasons). The other **31 of the 52 ready additions** were omitted
by the coverage scraper's first-cell traversal. Every reproduced entry and its
canonical candidate association appears in the JSON's `coverage_reconciliation`.

These are proposed additions, not applied data changes. The coverage issue's
exact comparison of unqualified table cells with branded curated slugs will
continue to produce false positives until the coverage collector is separately
updated; that collector is outside this task's ownership.

## Reproduce

```powershell
python -m app.ingest.cpu_audit `
  --page List_of_AMD_Ryzen_processors `
  --page List_of_AMD_Opteron_processors `
  --coverage-page List_of_AMD_Ryzen_processors `
  --coverage-page List_of_AMD_Opteron_processors `
  --coverage-page List_of_AMD_Epyc_processors `
  --data-root ../TechAPI/data `
  --output app/ingest/artifacts/amd-cpu-dry-run.json
```

For an offline replay, add `--html-dir PATH` containing the three downloaded
`List_of_AMD_*_processors.html` files. Without that flag, the engine's regular
Wikipedia fetcher downloads the pages using its declared user-agent.

## Validation

Repository-wide `ruff check app tests`, `mypy app`, and `python -m app.validate`
pass. The full suite passes: **517 tests**, with **77.16% coverage** against the
60% threshold. The focused parser/pipeline selection contains 36 passing tests.

Refs #99
