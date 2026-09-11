# X-GU.RU — Programmatic SEO & Content Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Public tooling for x-gu.ru: large-scale city/service page generation, exact SEO index control, full-corpus quality analysis and release-first production deployment.

> This repository is **not the complete private backend**. Backend-integrated commands expect the deployed `app.*` package and production CSV/config/API credentials.

> GitHub commits do **not** update production automatically. The production rollout is a separate SSH operation described in `server-opt/PRODUCTION_DEPLOY_RUNBOOK.md`.

## What this project does

x-gu.ru may physically contain tens of thousands of generated pages:

```text
/<city>/<service>/index.html
```

Physical existence is intentionally separated from search indexability:

```text
physical pages
    ↓
search evidence + page quality + internal graph
    ↓
reviewed exact-pair SEO policy
    ↓
indexable pages only
```

Historical project data recorded roughly **37,625 sitemap URLs** before the first index shrink and roughly **1,097 URLs** afterwards. The current architecture is stricter: policy v2 decides indexability for an exact `city/service` pair instead of opening the full cross-product of allowed cities and services.

Architecture: [`SEO_ARCHITECTURE.md`](SEO_ARCHITECTURE.md)  
Operational SEO workflow: [`SEO_OPERATIONS.md`](SEO_OPERATIONS.md)  
Production rollout: [`server-opt/PRODUCTION_DEPLOY_RUNBOOK.md`](server-opt/PRODUCTION_DEPLOY_RUNBOOK.md)

## SEO policy v1 and v2

Shared policy logic lives in `index_policy.py`.

Historical v1 matrix:

```json
{
  "policy_version": 1,
  "open_cities": ["moskva", "tver"],
  "open_services": ["prodvizhenie-saita", "seo-audit-saita"]
}
```

This opens every `open_cities × open_services` combination.

Current v2 exact-pair model:

```json
{
  "policy_version": 2,
  "open_cities": ["moskva", "tver"],
  "open_pairs": [
    "moskva/prodvizhenie-saita",
    "moskva/seo-audit-saita",
    "tver/prodvizhenie-saita"
  ]
}
```

Here `/tver/seo-audit-saita/` stays closed. `open_cities` controls city hubs; `open_pairs` controls commercial landing pages. A v2 pair cannot reference a closed city hub.

`server-opt/index_policy.example.json` is a protected v2 example with `example_only=true`. `server-opt/index_policy.baseline.json` preserves the historical v1 baseline for compatibility/emergency review only.

## One SEO decision across the stack

The same exact policy semantics are used by:

```text
index_policy.py
├── content_generator.py             -> robots at render time
├── server-opt/shrink_index.py       -> sitemap + bulk index/noindex
├── seo_healthcheck.py               -> expected robots/sitemap state
├── server-opt/rerender_open_hubs.py -> city-specific links
├── server-opt/rerender_hubs_home.py -> homepage + city hubs
└── seo_rebuild_broken.py            -> repair without reopening a global matrix
```

This prevents generator, sitemap and internal linking from making different decisions for the same URL.

## Full SEO diagnostics for thousands of pages

The preferred read-only production diagnostic is one command:

```bash
python server-opt/seo_snapshot.py \
  --root /var/www/x-gu.ru/current \
  --days 90
```

It creates a timestamped report directory under `/opt/p3-app/data/seo-snapshots/` and never calls production mutators such as shrink, purge, rerender or deploy.

The snapshot includes:

- combined Yandex/GSC/manual search evidence;
- targeted pair quality;
- full-corpus thin/orphan/duplicate checks;
- crawl depth and homepage reachability;
- same-service cross-city body similarity;
- Title/H1/Description template and intent similarity;
- current policy coverage cohorts;
- whitelist lifecycle review;
- review-only exact-pair v2 candidate;
- GSC query/page cannibalization;
- GSC growth opportunities (CTR, striking-distance, content growth);
- cannibalization review;
- one prioritized `seo_action_queue.json`.

The queue can recommend review actions such as `CANNIBALIZATION_REVIEW`, `IMPROVE_OPEN_PAGE`, `OPEN_REVIEW`, `CLOSE_REVIEW`, `EVIDENCE_MISMATCH_REVIEW`, `SNIPPET_REVIEW`, `STRIKING_DISTANCE` and `INTERNAL_LINKING`. It never applies index/noindex/redirect/canonical changes automatically.

## Search evidence → quality → review-only policy

Search evidence:

```bash
python server-opt/build_search_evidence.py
python server-opt/build_search_evidence.py --apply
```

Pair quality:

```bash
python server-opt/pair_quality_audit.py \
  --root /var/www/x-gu.ru/current
```

Review-only v2 candidate:

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

`build_pair_policy.py` requires fresh evidence/quality by default, rejects mismatched evidence snapshots and always emits `example_only=true`. A candidate cannot be used directly as production policy until it is manually reviewed, documented and promoted.

## Hardened generator

The public generator layer is installed into the private backend as a four-file set:

```text
content_generator.py
_content_generator_legacy.py
city_morphology.py
index_policy.py
```

`content_generator.py` is a safety facade around the preserved legacy implementation. It enforces:

- Jinja `autoescape=True`;
- canonical `server-opt/templates` preference;
- release-bound policy and whitelist;
- fail-closed policy behavior;
- v1/v2 exact indexability;
- release whitelist SHA-256 verification;
- shared Russian city morphology;
- fabricated testimonial data disabled before render;
- output sanitation that removes Review/AggregateRating/LocalBusiness blocks, synthetic KPI/review sections and unsupported proof claims.

Installer:

```bash
python server-opt/install_generator_facade.py
python server-opt/install_generator_facade.py --apply
```

It syntax-checks the complete four-file set, stages files, creates backups and rolls back already-replaced files on a partial failure.

## Canonical templates

Master templates exist at repository root and under `server-opt/templates/` for deployment compatibility. Repository validation requires both copies to remain byte-identical.

The hardened generator prefers canonical `server-opt/templates` and only keeps `app/templates` as an explicit compatibility fallback.

## Release-first production model

Normal rollout:

```text
read-only server discovery
  -> separate tooling checkout
  -> validate_repo.py
  -> read-only SEO snapshot
  -> review current SEO state
  -> isolated release candidate
  -> reviewed production policy + source whitelist
  -> candidate-only mutations/rerenders
  -> finalize candidate (Git SHA + full fingerprint)
  -> strict predeploy
  -> atomic deploy/bootstrap
  -> post-deploy checks
  -> retain rollback releases
```

Normal tooling refuses to mutate active `current`, candidates outside the release area, symlink candidates and finalized immutable releases.

## Self-contained release contract

A deployable release contains:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

- `.xgu-index-keep.json` stores normalized v1/v2 policy plus policy/whitelist provenance and hashes.
- `.xgu-whitelist.txt` is the canonical whitelist snapshot for that exact release.
- `.xgu-release.json` stores tooling Git SHA and a deterministic fingerprint of final release contents.

Policy + whitelist + HTML therefore switch and roll back together.

## Apply reviewed policy to a candidate

Dry-run first:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy /opt/p3-app/data/index_policy.json \
  --whitelist /opt/p3-app/data/whitelist.txt
```

Only after review:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy /opt/p3-app/data/index_policy.json \
  --whitelist /opt/p3-app/data/whitelist.txt \
  --apply
```

The resulting sitemap contains only exact policy URLs plus protected whitelist exceptions. Other physical pages receive `noindex` and are removed from sitemap.

## Candidate maintenance

Bulk tools are dry-run first and candidate-only during normal operation:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python server-opt/rerender_open_hubs.py --root "$RELEASE"
python server-opt/rerender_hubs_home.py --root "$RELEASE"
python seo_inplace_fix.py --root "$RELEASE"
python seo_title_extend.py --root "$RELEASE"
python seo_rebuild_broken.py --root "$RELEASE" --slug tula
```

`purge_closed_pages.py` removes a directory only after confirming it is outside candidate sitemap/whitelist, contains `index.html`, explicitly says `noindex`, and the release remains inactive immediately before deletion.

## Finalize and deploy

After all candidate mutations:

```bash
TOOLING_SHA="$(git rev-parse HEAD)"
python server-opt/finalize_release.py "$RELEASE" --tooling-revision "$TOOLING_SHA"
python server-opt/finalize_release.py "$RELEASE" --tooling-revision "$TOOLING_SHA" --apply
python server-opt/predeploy_check.py "$RELEASE"
python server-opt/deploy_release.py "$RELEASE"
python server-opt/deploy_release.py "$RELEASE" --apply
```

Finalization makes the candidate immutable. Predeploy verifies release fingerprint, policy digest, whitelist hash, robots/sitemap state, canonical URLs, metadata, JSON-LD and broken links. Deploy revalidates under a host-wide nonblocking release lock and atomically switches `current`.

If `current` is still a real directory, use the guarded one-time `bootstrap_release_layout.py` described in the production runbook; do not manually rename or overwrite live production.

## Validation

Local/server and GitHub CI use the same entrypoint:

```bash
python scripts/validate_repo.py
```

It runs:

- Python syntax compilation;
- Ruff fatal rules;
- standalone unit tests;
- `scripts/repo_healthcheck.py`;
- `scripts/seo_pipeline_healthcheck.py`.

GitHub-hosted Actions for this repository are currently known to fail before the first workflow step receives a runner. Therefore the new suite must be executed on a real workstation/server before merge/deploy until that repository-level Actions issue is resolved. Do **not** treat the badge as proof of a passing validation run while that condition remains.

## Nginx and server safety

`server-opt/nginx/x-gu.ru.conf` is the canonical vhost. Always run:

```bash
sudo nginx -t
```

before reload.

`deploy_release.py`, `bootstrap_release_layout.py` and `prune_releases.py` share a host-wide release lock. `pre-bootstrap-*` emergency backups are protected from normal pruning.

## Project map

```text
.
├── content_generator.py
├── _content_generator_legacy.py
├── city_morphology.py
├── index_policy.py
├── seo_healthcheck.py
├── release_safety.py
├── release_integrity.py
├── SEO_ARCHITECTURE.md
├── SEO_OPERATIONS.md
├── scripts/
│   ├── validate_repo.py
│   ├── repo_healthcheck.py
│   └── seo_pipeline_healthcheck.py
├── tests/
└── server-opt/
    ├── seo_snapshot.py
    ├── build_search_evidence.py
    ├── pair_quality_audit.py
    ├── index_coverage_review.py
    ├── programmatic_seo_audit.py
    ├── link_graph_cluster_audit.py
    ├── metadata_intent_audit.py
    ├── whitelist_lifecycle_report.py
    ├── build_pair_policy.py
    ├── gsc_cannibalization_report.py
    ├── gsc_opportunity_report.py
    ├── build_cannibalization_review.py
    ├── seo_action_queue.py
    ├── shrink_index.py
    ├── finalize_release.py
    ├── predeploy_check.py
    ├── deploy_release.py
    ├── bootstrap_release_layout.py
    ├── prune_releases.py
    └── PRODUCTION_DEPLOY_RUNBOOK.md
```

## Requirements

- Python 3.11+;
- Linux for production release-control helpers;
- Ruff for full repository validation;
- private `app.*` backend for integrated render/Search Console/Webmaster operations;
- reviewed production policy/whitelist and production city/service data.

Basic setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install ruff
cp .env.example .env
```

Never commit real credentials or API tokens.

## License

MIT — see [LICENSE](LICENSE).
