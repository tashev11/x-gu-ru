# X-GU.RU — Content & SEO Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Content generation, SEO auditing, index management and production-support tooling used around x-gu.ru.

## Architecture

This repository is the public tooling layer, **not the complete private backend**. Some rendering commands integrate with a deployed `app.*` package and production data files that are intentionally not published here.

The hardened generator is installed as a three-file set:

- `content_generator.py` — safety facade around rendering/indexability;
- `_content_generator_legacy.py` — preserved legacy implementation;
- `city_morphology.py` — shared Russian city morphology.

`server-opt/install_generator_facade.py` syntax-checks, stages, backs up and installs that set transactionally. A partial replacement failure triggers rollback.

The three master templates are kept at the repository root and under `server-opt/templates/` for current deployment compatibility. They must remain byte-identical. The hardened generator prefers `server-opt/templates`; `app/templates` is only a fallback for older deployments.

## Production rule: build a release, never patch `current`

Normal production flow:

```text
build complete candidate
    -> apply reviewed policy + source whitelist to candidate
    -> candidate receives its own policy + whitelist snapshots
    -> run candidate-only repair/rerender/migration commands
    -> strict offline predeploy validation
    -> atomically switch current symlink
    -> keep older self-contained releases for rollback
```

Bulk mutation commands are dry-run by default. With `--apply`, their normal target must be a direct child of:

```text
/var/www/x-gu.ru/releases/
```

and must not be the active `/var/www/x-gu.ru/current` target. `--unsafe-allow-active-current` is an emergency-only break-glass flag where supported.

Shared path/write protections live in `release_safety.py`. Text files are replaced via temp-file + `os.replace()` rather than direct in-place writes.

## Self-contained release contract

Every deployable release contains both:

```text
<release>/.xgu-index-keep.json
<release>/.xgu-whitelist.txt
```

`server-opt/shrink_index.py` creates them from the reviewed source inputs.

The policy manifest contains:

- `open_cities`;
- `open_services`;
- `policy_source`;
- `policy_sha256`;
- `whitelist_source`;
- `whitelist_sha256`;
- generation timestamp.

The whitelist snapshot contains sorted canonical `https://x-gu.ru/.../` URLs. Its SHA-256 must match the manifest.

This means an atomic `current` symlink switch changes **HTML + robots state + sitemap + index policy + protected whitelist** together. Rollback therefore restores the exact contract that was reviewed for that historical release rather than applying today's whitelist to yesterday's HTML.

The hardened generator normally resolves:

```text
/var/www/x-gu.ru/current/.xgu-index-keep.json
/var/www/x-gu.ru/current/.xgu-whitelist.txt
```

Candidate rendering can explicitly set `XGU_KEEP_CONFIG` and `XGU_WHITELIST` to the two files inside that candidate. The generator verifies the whitelist SHA before rendering.

The historical global `data/index_keep_config.json` and `data/whitelist.txt` are migration-only runtime fallbacks. They require **separate** explicit flags:

```text
XGU_ALLOW_LEGACY_KEEP_CONFIG=1
XGU_ALLOW_LEGACY_WHITELIST=1
```

Missing policy/whitelist is fail-closed in normal production.

## Reviewed source inputs

Typical reviewed sources before building a release:

```text
/opt/p3-app/data/index_policy.json
/opt/p3-app/data/whitelist.txt
```

`server-opt/index_policy.example.json` is schema/example data and is marked `example_only=true`; it cannot be used as a production policy.

`server-opt/index_policy.baseline.json` preserves the historical August 17, 2026 baseline. It is available only through explicit emergency `--use-builtin-policy` and should be reviewed against current GSC/Yandex data before reuse.

Policy/whitelist validation rejects malformed slugs, missing review metadata, non-canonical hosts, query/fragment URLs, unsafe traversal and unsupported path depth.

## Production sequence

For a detailed SSH/Claude operator procedure, use [`server-opt/PRODUCTION_DEPLOY_RUNBOOK.md`](server-opt/PRODUCTION_DEPLOY_RUNBOOK.md). The runbook starts with read-only diagnostics and deliberately stops if the live `current` path is not yet a symlink.

Example paths:

```bash
RELEASE=/var/www/x-gu.ru/releases/20260910-120000
POLICY=/opt/p3-app/data/index_policy.json
SOURCE_WHITELIST=/opt/p3-app/data/whitelist.txt
```

### 1. Validate repository tooling

```bash
python scripts/validate_repo.py
```

This is the same validation entrypoint used by GitHub Actions. It runs:

1. Python syntax compilation;
2. Ruff fatal rules (`E9`, `F63`, `F7`, `F82`);
3. standalone unit tests;
4. repository architecture/safety invariants.

A limited local check can skip Ruff only when Ruff is genuinely unavailable:

```bash
python scripts/validate_repo.py --skip-ruff
```

### 2. Preview policy application

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST"
```

Review counts for close/reopen operations and the reported source hashes.

### 3. Apply policy and create the release contract

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$SOURCE_WHITELIST" \
  --apply
```

This applies robots changes to the candidate, creates `.xgu-index-keep.json`, snapshots `.xgu-whitelist.txt`, and rebuilds the candidate sitemap. If any page or metadata write fails, discard/rebuild that candidate instead of deploying a partially modified tree.

After this step, normal candidate commands use the embedded whitelist. The source whitelist is no longer a runtime dependency of this release.

### 4. Candidate-only maintenance

Examples:

```bash
python server-opt/rerender_open_hubs.py --root "$RELEASE"
python server-opt/rerender_hubs_home.py --root "$RELEASE"
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python seo_inplace_fix.py --root "$RELEASE"
python seo_title_extend.py --root "$RELEASE"
python seo_rebuild_broken.py --root "$RELEASE" --slug tula
```

All are dry-run by default. Apply only after reviewing the plan:

```bash
python server-opt/rerender_open_hubs.py --root "$RELEASE" --apply
```

The backend-integrated rerender/rebuild/sanitizer commands import the installed hardened generator from `app.services.content_generator`; repository validation rejects a regression back to a potentially stale top-level `content_generator` import.

The rerender/rebuild tools bind `XGU_KEEP_CONFIG` and `XGU_WHITELIST` to the **candidate's own** files before rendering and verify whitelist integrity.

`rerender_hubs_home.py` additionally prevents a full rerender from repopulating homepage navigation with closed cities: homepage cities come from `open_cities`, and open city hubs expose only `open_services` plus per-city whitelist extras.

`seo_rebuild_broken.py` only accepts its reviewed broken-city set and refuses incomplete city/service/release-contract inputs before file creation.

### 5. Optional purge of closed pages

Preview:

```bash
python server-opt/purge_closed_pages.py --root "$RELEASE"
```

Apply:

```bash
python server-opt/purge_closed_pages.py --root "$RELEASE" --apply
```

Purge uses only the candidate's embedded whitelist snapshot. A directory can be deleted only when:

- its URL is absent from release sitemap + release whitelist;
- its own `index.html` exists;
- that file explicitly contains `noindex`.

The release activity guard is re-evaluated immediately before **every** destructive delete, so a candidate that became `current` during the operation is protected.

### 6. Strict predeploy gate

```bash
python server-opt/predeploy_check.py "$RELEASE"
```

The strict gate requires both embedded files from the exact release and validates their SHA/provenance before the HTML audit. It checks every HTML page against policy, including robots, sitemap membership, canonical, duplicates, JSON-LD and internal links.

Zero is the default tolerance for serious integrity failures; thresholds are documented in `.env.example`.

### 7. Preview and deploy

`deploy_release.py` runs structural validation and strict predeploy itself.

Preview:

```bash
python server-opt/deploy_release.py "$RELEASE"
```

Apply:

```bash
python server-opt/deploy_release.py "$RELEASE" --apply
```

A deployable release requires at least:

- `index.html`;
- `robots.txt`;
- `sitemap.xml`;
- `.xgu-index-keep.json`;
- `.xgu-whitelist.txt`.

Sitemap indexes must reference existing local shards on the canonical host. The final `current` switch uses atomic symlink replacement. The previous target is printed as the rollback target.

`--unsafe-skip-predeploy` is emergency-only.

### 8. Rollback

Use the previous release path printed by deploy:

```bash
python server-opt/deploy_release.py \
  /var/www/x-gu.ru/releases/<previous-release> \
  --apply
```

Because every release contains its own policy **and whitelist snapshot**, rollback restores the complete historical indexability contract.

### 9. Prune old releases

Preview:

```bash
python server-opt/prune_releases.py --keep 5
```

Apply only after stability is confirmed:

```bash
python server-opt/prune_releases.py --keep 5 --apply
```

Pruning only considers direct children of the releases root, protects `current`, and resolves `current` again immediately before each delete.

## Hardened generator behavior

The facade enforces:

- Jinja HTML auto-escaping;
- canonical repository template preference;
- release-bound index policy + whitelist;
- explicit-only legacy global fallbacks;
- fail-closed missing policy/whitelist;
- shared city morphology;
- removal/neutralization of generated `Review` / `AggregateRating`, generated city `LocalBusiness`, synthetic review sections, hash-derived KPI blocks and unsupported blanket proof claims.

The legacy implementation remains preserved for compatibility and reviewability, but legacy render paths are patched to call the hardened hooks.

## Install generator facade into the private backend

Preview:

```bash
python server-opt/install_generator_facade.py
```

Apply:

```bash
python server-opt/install_generator_facade.py --apply
```

The installer syntax-checks all source files before touching live targets, stages the full set, backs up existing files, replaces them, and atomically restores backups on a partial failure.

After installation, do a small render smoke-test against a candidate release before bulk rendering.

## Project structure

```text
.
├── .github/workflows/ci.yml
├── .env.example
├── release_safety.py
├── content_generator.py
├── _content_generator_legacy.py
├── city_morphology.py
├── seo_healthcheck.py
├── seo_inplace_fix.py
├── seo_rebuild_broken.py
├── seo_title_extend.py
├── scripts/
│   ├── validate_repo.py
│   └── repo_healthcheck.py
├── tests/
└── server-opt/
    ├── PRODUCTION_DEPLOY_RUNBOOK.md
    ├── templates/
    ├── nginx/
    ├── index_policy.example.json
    ├── index_policy.baseline.json
    ├── shrink_index.py
    ├── predeploy_check.py
    ├── deploy_release.py
    ├── prune_releases.py
    ├── purge_closed_pages.py
    ├── sanitize_generated_proof.py
    └── install_generator_facade.py
```

## Requirements

- Python **3.11+**;
- Ruff for full repository validation;
- private `app.*` package for backend-integrated rendering commands;
- production CSV/config files for full render/index jobs.

Basic local setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install ruff
cp .env.example .env
```

Never commit populated `.env` files, API tokens, credentials or production secrets.

## Nginx/server hardening

`server-opt/nginx/x-gu.ru.conf` is the only canonical vhost copy in the repository. The configuration includes:

- canonical `www.x-gu.ru -> https://x-gu.ru` redirects;
- HTTP -> HTTPS canonical redirect;
- `server_tokens off`;
- baseline browser security headers;
- request-size and rate limiting for `/api/v1/leads/submit`;
- HTTP 429 for rate-limit rejection;
- long-lived static asset caching.

Deploy the global and vhost configs as a pair and always validate before reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

`/console` is proxied to the private backend. It must be authenticated/authorized there or restricted through Nginx/VPN/IP before production exposure.

## Disk/log safety

Cleanup scripts do not truncate active authentication/application logs. Journal retention is configurable. Release retention should be used instead of accumulating unlimited candidates, especially on a disk-constrained server.

Increasing disk capacity remains preferable to increasingly aggressive destruction of operational history.

## Security and operating rules

- Never commit secrets or real `.env` files.
- Do not bulk-edit the active `current` tree in normal operation.
- Treat `--unsafe-*` flags as emergency-only break-glass controls.
- Review dry-run counts before every `--apply`.
- Never deploy a release that failed predeploy validation.
- Nginx limits complement but do not replace backend payload validation/anti-spam.
- Privileged backend routes require independent authentication/authorization.
- Merge security-sensitive changes only after the validation suite has actually executed successfully.

## License

MIT License — see [LICENSE](LICENSE).
