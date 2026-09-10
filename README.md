# X-GU.RU — Programmatic SEO & Content Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Public tooling around x-gu.ru for large-scale city/service page generation, SEO index control, quality auditing and safe production releases.

> This repository is **not the complete private backend**. Backend-integrated commands expect the deployed `app.*` package and production CSV/config data.

## The core SEO idea

x-gu.ru may physically store tens of thousands of generated pages such as:

```text
/<city>/<service>/index.html
```

That does **not** mean all of those URLs should be submitted for indexing.

The project separates three things:

```text
physical pages
    ↓
search demand + page quality
    ↓
reviewed index policy
    ↓
indexable pages only
```

Historical repository data recorded roughly 37,625 sitemap URLs before the first index shrink and roughly 1,097 afterwards. The current goal is more precise than that historical shrink: use policy v2 to decide indexability for the exact `city/service` pair rather than blindly opening every combination of a good city and a good service.

See [`SEO_ARCHITECTURE.md`](SEO_ARCHITECTURE.md) for the full model.

## SEO policy v1 vs v2

Shared policy logic lives in `index_policy.py` and is used by generator, sitemap builder, healthcheck and hub re-rendering.

### v1 — historical matrix

```json
{
  "policy_version": 1,
  "open_cities": ["moskva", "tver"],
  "open_services": ["prodvizhenie-saita", "seo-audit-saita"]
}
```

This opens all four combinations.

### v2 — exact pairs

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

Here `/tver/seo-audit-saita/` stays closed even though both `tver` and `seo-audit-saita` exist elsewhere in the site.

`open_cities` controls indexable city hubs. `open_pairs` controls exact commercial landings. A v2 pair cannot reference a city hub that is not in `open_cities`.

`server-opt/index_policy.example.json` demonstrates v2. The historical emergency baseline remains v1 for compatibility only.

## SEO decision pipeline for thousands of pages

### 1. Collect real search evidence

```bash
python server-opt/build_search_evidence.py
python server-opt/build_search_evidence.py --apply
```

The tool combines:

- Yandex Webmaster URLs already seen in search;
- Google Search Console page impressions/clicks with pagination;
- manually protected business-critical URLs.

It writes review data only:

```text
/opt/p3-app/data/search_evidence.json
/opt/p3-app/data/whitelist.candidate.txt
```

It does **not** replace the production whitelist.

### 2. Audit the quality of search-evidence pairs

```bash
python server-opt/pair_quality_audit.py \
  --root /var/www/x-gu.ru/current
```

This inspects every `city/service` URL present in search evidence even if the page is currently `noindex`.

It checks:

- page exists and is readable;
- visible word count;
- Title, Description and H1;
- self-canonical;
- JSON-LD validity;
- exact duplicate visible bodies;
- near-duplicate bodies.

Output:

```text
/opt/p3-app/data/pair_quality.json
```

Hard defects such as missing page, thin content, bad canonical, invalid JSON-LD or an exact duplicate are marked `improve_before_index`. Near-duplicates are marked for manual similarity review rather than being automatically condemned.

### 3. Build a review-only exact-pair candidate

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

By default it requires both `search_evidence.json` and `pair_quality.json`.

The resulting candidate intersects search value with page quality and writes:

```text
/opt/p3-app/data/index_policy.v2.candidate.json
/opt/p3-app/data/index_policy.v2.review.json
```

The generated policy always contains:

```json
"example_only": true
```

Therefore `shrink_index.py` refuses to use it directly in production. A human/operator must review the exact pairs, fix weak pages, set `example_only=false`, add `reviewed_at`, and record the decision source in `source_note` before promotion to `/opt/p3-app/data/index_policy.json`.

### 4. Audit the whole currently indexable corpus

```bash
python server-opt/programmatic_seo_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-programmatic-seo.json
```

This reports:

- physical pages;
- expected indexable pages;
- policy-closed pages;
- thin indexable pages;
- orphan indexable pages;
- links from open pages to closed pages;
- exact duplicate bodies;
- near-duplicate bodies.

### 5. Consolidated production SEO report

```bash
SEO_REPORT_DAYS=90 python server-opt/seo_report.py
```

The report shows:

- policy version and mode (`matrix` or `pairs`);
- number of open city hubs;
- number of exact service pairs;
- physical/open/closed page inventory;
- Yandex URLs inside/outside current policy;
- Google URLs inside/outside current policy;
- open pages with zero GSC page signal;
- duplicate/thin/orphan indicators.

The important KPI is **not how many pages were generated**. It is how many useful URL-level search assets are indexed, receive impressions/clicks and convert without polluting the site with low-value near-duplicates.

## Hardened generator

The public generator layer is installed into the private backend as a four-file set:

```text
content_generator.py
_content_generator_legacy.py
city_morphology.py
index_policy.py
```

`content_generator.py` is a safety facade around the preserved legacy implementation. It provides:

- Jinja `autoescape=True`;
- canonical `server-opt/templates` preference;
- release-bound policy and whitelist;
- fail-closed behavior when SEO policy is unavailable;
- policy v1/v2 exact indexability;
- whitelist SHA-256 verification;
- shared Russian city morphology;
- fabricated testimonial/review data disabled before render;
- output sanitizer as a second defense against generated Review/AggregateRating/LocalBusiness and synthetic KPI/proof blocks.

Transactional install:

```bash
python server-opt/install_generator_facade.py
python server-opt/install_generator_facade.py --apply
```

The installer syntax-checks all four sources, stages the complete set, creates backups and rolls back already-replaced files if installation fails midway.

## One SEO source of truth

The same exact pair decision is consumed by:

```text
index_policy.py
├── content_generator.py             -> robots at render time
├── server-opt/shrink_index.py       -> sitemap + bulk index/noindex
├── seo_healthcheck.py               -> expected robots/sitemap state
├── server-opt/rerender_open_hubs.py -> links for each open city
├── server-opt/rerender_hubs_home.py -> homepage + city hubs
└── seo_rebuild_broken.py            -> repair without reopening the global matrix
```

For policy v2 a city hub links only to services allowed for that city plus explicit whitelist exceptions. This prevents internal linking from silently reopening the old cross-product.

## Canonical templates

Master templates exist at repository root and under `server-opt/templates/` for deployment compatibility. Repository validation requires the two copies to remain byte-identical.

Canonical templates avoid Tailwind Play CDN, city hubs consume `robots_content`, and landing pages use the real privacy route.

## Release-first production model

Normal flow:

```text
build/copy candidate
  -> reviewed index policy + source whitelist
  -> candidate-only mutations/rerenders
  -> full SEO checks
  -> finalize candidate
  -> strict predeploy
  -> atomic symlink switch
  -> post-deploy SEO report
  -> retain rollback releases
```

Writable candidates are direct real directories under `/var/www/x-gu.ru/releases/`. Normal tooling refuses to mutate active `current`, candidates outside releases, symlink candidates and already-finalized releases.

## Self-contained release contract

A deployable release contains:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

### `.xgu-index-keep.json`

Created by `server-opt/shrink_index.py`. It contains the normalized policy plus policy/whitelist provenance and SHA-256 hashes. For v2 the authoritative landing list is `open_pairs`; `open_services` is retained only as derived inventory metadata.

### `.xgu-whitelist.txt`

Canonical sorted whitelist snapshot belonging to that exact release.

### `.xgu-release.json`

Created after all candidate mutations by `server-opt/finalize_release.py`. It stores exact tooling Git SHA and a deterministic fingerprint of release contents. Any subsequent mutation invalidates strict predeploy.

## Apply reviewed policy to an isolated candidate

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy /opt/p3-app/data/index_policy.json \
  --whitelist /opt/p3-app/data/whitelist.txt
```

Review the dry-run inventory first. Only then:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy /opt/p3-app/data/index_policy.json \
  --whitelist /opt/p3-app/data/whitelist.txt \
  --apply
```

The resulting sitemap contains only URLs permitted by the exact policy plus protected whitelist exceptions. All other physical pages are moved to `noindex` and removed from sitemap.

`server-opt/index_policy.baseline.json` is an explicit emergency v1 fallback only via `--use-builtin-policy`.

## Candidate maintenance

All bulk tools are dry-run first and release-candidate only during normal operation:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python server-opt/rerender_open_hubs.py --root "$RELEASE"
python server-opt/rerender_hubs_home.py --root "$RELEASE"
python seo_inplace_fix.py --root "$RELEASE"
python seo_title_extend.py --root "$RELEASE"
python seo_rebuild_broken.py --root "$RELEASE" --slug tula
```

`purge_closed_pages.py` physically removes a directory only if the URL is outside candidate sitemap/whitelist, `index.html` exists and explicitly says `noindex`, and the release is still inactive immediately before deletion.

## Finalize, predeploy, deploy

After all mutations:

```bash
TOOLING_SHA="$(git rev-parse HEAD)"
python server-opt/finalize_release.py "$RELEASE" --tooling-revision "$TOOLING_SHA"
python server-opt/finalize_release.py "$RELEASE" --tooling-revision "$TOOLING_SHA" --apply
```

Then:

```bash
python server-opt/predeploy_check.py "$RELEASE"
python server-opt/deploy_release.py "$RELEASE"
python server-opt/deploy_release.py "$RELEASE" --apply
```

Predeploy verifies release fingerprint, whitelist hash, **canonical policy digest**, exact robots/sitemap state, canonical URLs, metadata, JSON-LD and broken links. Deploy repeats structural policy validation even when its emergency predeploy skip is explicitly requested.

The production `current` change is an atomic symlink replacement. Deploy/bootstrap/prune share one non-blocking host-wide release lock.

## First server migration

If `/var/www/x-gu.ru/current` is a real directory rather than a symlink, use the guarded one-time bootstrap after building and validating a separate candidate:

```bash
python server-opt/bootstrap_release_layout.py "$RELEASE"
python server-opt/bootstrap_release_layout.py "$RELEASE" --apply
```

The legacy current directory is retained as a protected `pre-bootstrap-*` emergency backup.

## Release retention

```bash
python server-opt/prune_releases.py --keep 5
python server-opt/prune_releases.py --keep 5 --apply
```

The active release is rechecked before deletion. Bootstrap backups remain protected unless explicitly included.

## Validation

Local and GitHub CI use the same entrypoint:

```bash
python scripts/validate_repo.py
```

It runs syntax compilation, Ruff fatal rules, standalone unit tests and repository architecture invariants.

GitHub-hosted Actions for this repository are currently known to fail before the first workflow step is assigned a runner, so server/local validation is mandatory before merge/deploy until that external repository-level problem is resolved.

## Server / Claude procedure

Use:

[`server-opt/PRODUCTION_DEPLOY_RUNBOOK.md`](server-opt/PRODUCTION_DEPLOY_RUNBOOK.md)

GitHub changes do not automatically modify the production server.

## Nginx

`server-opt/nginx/x-gu.ru.conf` is the canonical vhost. Before any reload:

```bash
sudo nginx -t
```

Only after success:

```bash
sudo systemctl reload nginx
```

## Project structure

```text
.
├── content_generator.py
├── _content_generator_legacy.py
├── city_morphology.py
├── index_policy.py
├── SEO_ARCHITECTURE.md
├── seo_healthcheck.py
├── release_safety.py
├── release_integrity.py
├── scripts/
│   ├── validate_repo.py
│   └── repo_healthcheck.py
├── tests/
└── server-opt/
    ├── build_search_evidence.py
    ├── pair_quality_audit.py
    ├── build_pair_policy.py
    ├── programmatic_seo_audit.py
    ├── seo_report.py
    ├── shrink_index.py
    ├── finalize_release.py
    ├── predeploy_check.py
    ├── deploy_release.py
    ├── bootstrap_release_layout.py
    ├── prune_releases.py
    ├── purge_closed_pages.py
    └── PRODUCTION_DEPLOY_RUNBOOK.md
```

## Requirements

- Python 3.11+;
- Linux for production release-control helpers;
- Ruff for full repository validation;
- private `app.*` backend for integrated render/Search Console/Webmaster operations;
- production city/service CSV and reviewed policy data.

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
