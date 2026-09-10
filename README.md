# X-GU.RU — Content & SEO Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Public content-generation, SEO validation, index-policy and production-safety tooling used around x-gu.ru.

> This repository is **not the complete private backend**. Backend-integrated commands expect the deployed `app.*` package and production CSV/config data.

## What this repository now protects

The tooling is built around five rules:

1. generated pages fail closed when index policy is unavailable or inconsistent;
2. bulk changes happen in an isolated release candidate, never directly in live `current` during normal operation;
3. every deployable release carries its own policy and whitelist snapshots;
4. a release is finalized with the exact tooling Git SHA and a deterministic content fingerprint;
5. deploy/bootstrap/prune are serialized by one host-wide lock and production switches use atomic filesystem operations.

## Hardened generator

The generator is installed into the private backend as a three-file set:

```text
content_generator.py
_content_generator_legacy.py
city_morphology.py
```

`content_generator.py` is a safety facade around the preserved legacy implementation. It provides:

- Jinja `autoescape=True`;
- canonical `server-opt/templates` preference;
- release-bound index policy and whitelist;
- fail-closed policy handling;
- whitelist SHA-256 verification;
- shared Russian city morphology;
- fabricated testimonial/review data disabled before render;
- output sanitizer as a second defense against generated `Review`, `AggregateRating`, generated city `LocalBusiness`, synthetic KPI/proof blocks and unsupported blanket claims.

The facade can be installed transactionally with:

```bash
python server-opt/install_generator_facade.py
python server-opt/install_generator_facade.py --apply
```

The installer syntax-checks all three sources, stages the full set, makes backups and rolls back already-replaced files if installation fails midway.

## Canonical templates

The master templates exist at repository root and under `server-opt/templates/` for deployment compatibility. The two copies must remain byte-identical; repository validation enforces this.

The canonical templates do not use Tailwind Play CDN, the city hub consumes `robots_content`, and the landing template uses the real privacy route.

## Release-first production model

Normal flow:

```text
build/copy candidate
  -> reviewed index policy + source whitelist
  -> candidate-only mutations/rerenders
  -> finalize candidate
  -> strict predeploy
  -> atomic symlink switch
  -> post-deploy checks
  -> retain rollback releases
```

Normal writable candidates are direct real directories under:

```text
/var/www/x-gu.ru/releases/
```

`release_safety.py` rejects:

- active `current` as a normal mutation target;
- candidates outside `releases/`;
- symlink candidates;
- finalized candidates;
- direct text replacement through shared production helpers when a safer atomic replace is expected.

Most mutators are dry-run by default and require explicit `--apply`.

## Self-contained release contract

A deployable release contains three metadata files:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

### `.xgu-index-keep.json`

Created by `server-opt/shrink_index.py`. It records:

- `open_cities`;
- `open_services`;
- policy source and SHA-256;
- whitelist source and SHA-256;
- generation timestamp.

### `.xgu-whitelist.txt`

Canonical, sorted whitelist snapshot copied into that exact release. Runtime render/SEO behavior no longer depends on whatever the global whitelist happens to contain later.

### `.xgu-release.json`

Created **after all candidate mutations** by `server-opt/finalize_release.py`. It records:

- contract version;
- full 40-character tooling Git SHA;
- finalization time;
- source release;
- deterministic SHA-256 of release contents;
- file count;
- total bytes.

The fingerprint covers every regular release file except `.xgu-release.json` itself. Symlinks inside a finalized static release are rejected.

After `.xgu-release.json` exists, bulk mutation tools reject the candidate. A changed/new/deleted file after finalization causes predeploy/deploy fingerprint verification to fail.

## Index policy

Normal reviewed policy source:

```text
/opt/p3-app/data/index_policy.json
```

Source whitelist:

```text
/opt/p3-app/data/whitelist.txt
```

Apply them to an isolated candidate:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy /opt/p3-app/data/index_policy.json \
  --whitelist /opt/p3-app/data/whitelist.txt
```

Review dry-run counts, then:

```bash
python server-opt/shrink_index.py \
  --web-root "$RELEASE" \
  --policy /opt/p3-app/data/index_policy.json \
  --whitelist /opt/p3-app/data/whitelist.txt \
  --apply
```

`server-opt/index_policy.example.json` is marked `example_only=true` and cannot be used as production policy.

`server-opt/index_policy.baseline.json` preserves a historical emergency baseline. It is available only through explicit `--use-builtin-policy` and should be reviewed against current search-console data before reuse.

## Candidate maintenance

Examples, all dry-run first:

```bash
python server-opt/sanitize_generated_proof.py --root "$RELEASE"
python server-opt/rerender_open_hubs.py --root "$RELEASE"
python server-opt/rerender_hubs_home.py --root "$RELEASE"
python seo_inplace_fix.py --root "$RELEASE"
python seo_title_extend.py --root "$RELEASE"
python seo_rebuild_broken.py --root "$RELEASE" --slug tula
```

Backend-integrated rerender/rebuild/sanitizer scripts import the installed hardened generator from `app.services.content_generator`, not from an accidental top-level file.

`rerender_hubs_home.py` keeps homepage navigation restricted to `open_cities` and limits open city hubs to `open_services + whitelist extras`.

`purge_closed_pages.py` deletes a directory only if all conditions agree:

- URL is absent from candidate sitemap + candidate whitelist;
- `index.html` exists;
- that HTML explicitly has `noindex`;
- release is still inactive immediately before deletion.

## Finalize a release

After **all** mutations:

```bash
TOOLING_SHA="$(git rev-parse HEAD)"

python server-opt/finalize_release.py \
  "$RELEASE" \
  --tooling-revision "$TOOLING_SHA"
```

Review file count/bytes/content hash, then:

```bash
python server-opt/finalize_release.py \
  "$RELEASE" \
  --tooling-revision "$TOOLING_SHA" \
  --apply
```

Do not mutate the candidate after this point.

## Strict predeploy

```bash
python server-opt/predeploy_check.py "$RELEASE"
```

The strict gate verifies:

- `.xgu-release.json` content fingerprint and tooling revision;
- policy + whitelist provenance/hash;
- complete policy coverage of generated HTML;
- index/noindex state;
- sitemap membership and orphan URLs;
- canonical domain/mismatch/duplicates;
- title/description/H1/lang/OG checks;
- JSON-LD validity;
- broken internal page links.

Serious release-integrity thresholds default to zero tolerated failures.

## Deploy and rollback

Once `current` is a symlink:

```bash
python server-opt/deploy_release.py "$RELEASE"
python server-opt/deploy_release.py "$RELEASE" --apply
```

The apply path re-runs validation/predeploy while holding the host-wide lock:

```text
/var/www/x-gu.ru/.release-operation.lock
```

The lock is exclusive, non-blocking and opened without following a symlink. A concurrent deploy/bootstrap/prune is rejected instead of waiting to execute later against stale state.

The final `current` change is an atomic symlink replacement. The previous target is printed as the rollback target and can be passed back to `deploy_release.py`.

## First migration from a real `current` directory

If `/var/www/x-gu.ru/current` is not yet a symlink, do **not** rename it manually and do not use normal `deploy_release.py`.

Build a separate candidate from a copy of live static files, apply policy, run all maintenance, finalize it and pass strict predeploy. Then use:

```bash
python server-opt/bootstrap_release_layout.py "$RELEASE"
python server-opt/bootstrap_release_layout.py "$RELEASE" --apply
```

Under the same host-wide lock, bootstrap revalidates the finalized target, moves the old real `current` directory to a `pre-bootstrap-*` emergency backup and installs the symlink to the new candidate. If symlink installation fails after the move, it attempts to restore the old directory automatically.

`pre-bootstrap-*` backups are excluded from normal release pruning by default.

## Release retention

Preview:

```bash
python server-opt/prune_releases.py --keep 5
```

Apply later, after release stability is confirmed:

```bash
python server-opt/prune_releases.py --keep 5 --apply
```

The plan is rebuilt under the host-wide lock. Active release is rechecked before deletion. `pre-bootstrap-*` stays protected unless the operator explicitly passes `--include-bootstrap-backups`.

## Validation

The same entrypoint is used locally and by GitHub Actions:

```bash
python scripts/validate_repo.py
```

It runs:

1. Python syntax compilation;
2. Ruff fatal rules (`E9`, `F63`, `F7`, `F82`);
3. standalone `unittest` regression suite;
4. `scripts/repo_healthcheck.py` architecture/safety invariants.

A local diagnostic-only run can skip Ruff when it is genuinely unavailable:

```bash
python scripts/validate_repo.py --skip-ruff
```

## Server/Claude deployment procedure

Use the complete operator runbook:

[`server-opt/PRODUCTION_DEPLOY_RUNBOOK.md`](server-opt/PRODUCTION_DEPLOY_RUNBOOK.md)

It supports both:

- guarded one-time bootstrap from legacy real `current`;
- normal subsequent immutable release deployment.

It records the exact validated tooling SHA and release content fingerprint so the deployed version can be identified later.

## Nginx

`server-opt/nginx/x-gu.ru.conf` is the canonical vhost. The checked configuration includes:

- HTTP and `www` canonical redirects to `https://x-gu.ru`;
- `server_tokens off`;
- baseline browser security headers;
- exact rate/body limits for `/api/v1/leads/submit`;
- HTTP 429 on rate limit;
- static asset caching.

Before reload:

```bash
sudo nginx -t
```

Only on success:

```bash
sudo systemctl reload nginx
```

`/console` is proxied to the private backend and must be independently authenticated/authorized or restricted by VPN/IP/Nginx controls.

## Cleanup safety

Cleanup scripts do not truncate active authentication/application logs. Journal retention is configurable. Increasing disk capacity is preferable to increasingly aggressive deletion of operational history.

## Project structure

```text
.
├── content_generator.py
├── _content_generator_legacy.py
├── city_morphology.py
├── release_safety.py
├── release_integrity.py
├── seo_healthcheck.py
├── seo_inplace_fix.py
├── seo_title_extend.py
├── seo_rebuild_broken.py
├── scripts/
│   ├── validate_repo.py
│   └── repo_healthcheck.py
├── tests/
└── server-opt/
    ├── templates/
    ├── nginx/
    ├── shrink_index.py
    ├── finalize_release.py
    ├── predeploy_check.py
    ├── deploy_release.py
    ├── bootstrap_release_layout.py
    ├── prune_releases.py
    ├── purge_closed_pages.py
    ├── install_generator_facade.py
    └── PRODUCTION_DEPLOY_RUNBOOK.md
```

## Requirements

- Python 3.11+;
- Linux for production release-control helpers (`flock`/filesystem semantics);
- Ruff for full repository validation;
- private `app.*` package for backend-integrated render commands;
- production CSV/config files for full generation/index jobs.

Basic setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install ruff
cp .env.example .env
```

Never commit real `.env` values, credentials or API tokens.

## License

MIT — see [LICENSE](LICENSE).
