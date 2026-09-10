# X-GU.RU — Content & SEO Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Content generation, SEO auditing, index management and production-support tooling used around x-gu.ru.

## Architecture

This repository is the public tooling layer, **not the complete private backend**. Some rendering commands integrate with a deployed `app.*` package and production data files that are intentionally not published here.

The hardened generator is split into three files that are installed together:

- `content_generator.py` — safety facade around rendering/indexability;
- `_content_generator_legacy.py` — preserved legacy implementation;
- `city_morphology.py` — shared Russian city morphology.

`server-opt/install_generator_facade.py` stages, syntax-checks, backs up and installs those three files as one set. A partial installation failure triggers rollback.

The repository keeps compatibility copies of the three master templates at the root and under `server-opt/templates/`. They must remain byte-identical. The hardened generator prefers `server-opt/templates`; `app/templates` is only a compatibility fallback.

## Core production rule: release first, mutate second

Bulk SEO/render/repair tools must **not normally modify the active** `/var/www/x-gu.ru/current` tree.

The production model is:

```text
build complete candidate
    -> apply reviewed index policy to candidate
    -> run candidate-only repairs/rerenders/migrations
    -> strict offline predeploy audit
    -> atomic current symlink switch
    -> retain rollback releases
```

`release_safety.py` is the shared guard used by bulk mutators. Normal `--apply` targets must be direct children of:

```text
/var/www/x-gu.ru/releases/
```

and must not be the active `current` target. Commands that support an active-current emergency override name it explicitly as `--unsafe-allow-active-current`. That override is not part of normal deployment.

Text mutations use an atomic temp-file + `os.replace()` helper instead of direct `write_text()` replacement of live candidate files.

## Release-bound index policy

The reviewed source policy normally lives outside the web root:

```text
/opt/p3-app/data/index_policy.json
```

`server-opt/shrink_index.py` validates that policy and produces this manifest inside the release candidate:

```text
<release>/.xgu-index-keep.json
```

The manifest records:

- `open_cities`;
- `open_services`;
- `policy_source`;
- `policy_sha256`;
- generation timestamp.

Because `current` is a symlink to a release directory, switching releases now switches **HTML + sitemap + robots state + index policy together**.

The generator resolves `/var/www/x-gu.ru/current/.xgu-index-keep.json` by default. Candidate rendering can explicitly set `XGU_KEEP_CONFIG` to the candidate manifest. The old global `data/index_keep_config.json` is migration-only and is accepted only when `XGU_ALLOW_LEGACY_KEEP_CONFIG=1` is deliberately set.

Missing policy and missing whitelist are fail-closed conditions. `XGU_ALLOW_MISSING_KEEP_CONFIG=1` is also migration/local-only and should remain unset in normal production.

## Reviewed policy files

`server-opt/index_policy.example.json` is schema/example data and has `example_only=true`; it cannot be used as a production policy.

`server-opt/index_policy.baseline.json` preserves the historical reviewed baseline from August 17, 2026. It is an emergency recovery reference, not a claim that those cities/services remain optimal. Using it requires the explicit `--use-builtin-policy` flag.

Production policy validation rejects malformed slugs, missing review metadata, unsafe whitelist paths, non-canonical hosts, query strings/fragments and path traversal.

## Recommended production sequence

Assume a fully built static candidate already exists at:

```bash
RELEASE=/var/www/x-gu.ru/releases/20260910-120000
POLICY=/opt/p3-app/data/index_policy.json
WHITELIST=/opt/p3-app/data/whitelist.txt
```

### 1. Validate repository tooling

Run the same validator used by CI:

```bash
python scripts/validate_repo.py
```

It runs:

1. Python syntax compilation;
2. Ruff fatal-error rules (`E9`, `F63`, `F7`, `F82`);
3. standalone `unittest` regression tests;
4. repository architecture/safety invariants.

A limited local run without Ruff is available only when Ruff genuinely cannot be installed:

```bash
python scripts/validate_repo.py --skip-ruff
```

### 2. Dry-run the reviewed index policy against the candidate

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$WHITELIST"
```

Review the page counts and close/reopen plan.

### 3. Apply policy to the candidate

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy "$POLICY" \
  --whitelist "$WHITELIST" \
  --apply
```

This modifies only the candidate, writes its `.xgu-index-keep.json`, and rebuilds its sitemap. A failed page mutation stops the operation; the candidate should then be discarded/rebuilt rather than deployed.

### 4. Run candidate-only maintenance when needed

Examples:

```bash
python server-opt/rerender_open_hubs.py --root "$RELEASE"
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python seo_inplace_fix.py --root "$RELEASE"
python seo_title_extend.py --root "$RELEASE"
```

Those commands are dry-run by default. Apply only after reviewing their plans:

```bash
python server-opt/rerender_open_hubs.py --root "$RELEASE" --apply
python server-opt/sanitize_generated_proof.py --root "$RELEASE" --apply
```

Candidate render/rebuild commands require the candidate's `.xgu-index-keep.json` and bind `XGU_KEEP_CONFIG`/`XGU_WHITELIST` to that candidate before rendering, so generated robots state cannot accidentally come from the previous active release.

`seo_rebuild_broken.py` additionally restricts rebuilds to its reviewed city set and refuses missing city/service/policy inputs before creating files.

### 5. Optional purge of closed pages

Preview:

```bash
python server-opt/purge_closed_pages.py \
  --root "$RELEASE" \
  --whitelist "$WHITELIST"
```

Apply:

```bash
python server-opt/purge_closed_pages.py \
  --root "$RELEASE" \
  --whitelist "$WHITELIST" \
  --apply
```

Deletion requires all safeguards to agree: the URL is absent from sitemap/whitelist, `index.html` exists, and the page explicitly contains `noindex`. The release target is re-checked immediately before every destructive delete so a candidate that became active during the operation is protected.

### 6. Run the strict predeploy gate

```bash
python server-opt/predeploy_check.py \
  "$RELEASE" \
  --whitelist "$WHITELIST"
```

The strict gate requires the **manifest inside that exact release** and validates every HTML page against policy. Among other checks it covers:

- title, description, H1, language and OG metadata;
- canonical domain and canonical-to-page match;
- duplicate title/H1/canonical groups;
- JSON-LD validity;
- internal broken page links;
- open page accidentally `noindex`;
- closed page accidentally indexable;
- open page missing from sitemap;
- closed page leaking into sitemap;
- malformed/non-canonical sitemap URLs;
- sitemap URLs with no corresponding release page.

Zero is the default tolerance for serious integrity failures; thresholds are documented in `.env.example`.

### 7. Preview and deploy

`deploy_release.py` runs structural validation and the strict predeploy gate itself, so operators cannot normally forget the gate.

Preview:

```bash
python server-opt/deploy_release.py \
  "$RELEASE" \
  --whitelist "$WHITELIST"
```

Apply:

```bash
python server-opt/deploy_release.py \
  "$RELEASE" \
  --whitelist "$WHITELIST" \
  --apply
```

The deploy helper requires the candidate to be a direct child of the releases root and requires at least:

- `index.html`;
- `robots.txt`;
- `sitemap.xml`;
- `.xgu-index-keep.json`.

Sitemap indexes must point to existing local shard files on the canonical host. The final `current` switch is an atomic symlink replacement. The previous target is printed as the rollback target.

`--unsafe-skip-predeploy` exists only for emergency recovery and should not be used in the normal pipeline.

### 8. Rollback

Use the previous release path printed by deploy:

```bash
python server-opt/deploy_release.py \
  /var/www/x-gu.ru/releases/<previous-release> \
  --whitelist "$WHITELIST" \
  --apply
```

Because each release contains its own policy manifest, rollback restores HTML and index policy together.

### 9. Prune old releases

Preview:

```bash
python server-opt/prune_releases.py --keep 5
```

Apply only after review:

```bash
python server-opt/prune_releases.py --keep 5 --apply
```

Pruning only considers direct children of the releases root, protects `current`, and resolves `current` again immediately before each delete to close the race between planning and deletion.

## Hardened generator behavior

The facade enforces:

- Jinja HTML auto-escaping;
- canonical repository template preference;
- release-bound index policy;
- explicit-only legacy keep-config migration fallback;
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
├── release_safety.py                 # shared candidate/active guards + atomic writes
├── content_generator.py              # hardened generator facade
├── _content_generator_legacy.py      # preserved legacy implementation
├── city_morphology.py                # shared city morphology
├── seo_healthcheck.py                # offline release/deployed SEO audit
├── seo_inplace_fix.py                # release-first grammar repair
├── seo_rebuild_broken.py             # release-first targeted rebuild
├── seo_title_extend.py                # release-first title repair
├── scripts/
│   ├── validate_repo.py              # local + CI validation entrypoint
│   └── repo_healthcheck.py           # architecture/safety invariants
├── tests/                             # standalone regression tests
└── server-opt/
    ├── templates/                     # canonical Jinja templates
    ├── nginx/                         # canonical Nginx config
    ├── index_policy.example.json
    ├── index_policy.baseline.json
    ├── shrink_index.py                # policy -> candidate manifest/sitemap/robots
    ├── predeploy_check.py             # strict immutable-candidate gate
    ├── deploy_release.py              # validated atomic symlink switch
    ├── prune_releases.py              # race-safe retention
    ├── purge_closed_pages.py          # guarded candidate-only deletion
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
