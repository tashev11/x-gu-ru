# X-GU.RU — Content & SEO Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Content generation, SEO auditing, index management and production-support tooling used around [x-gu.ru](https://x-gu.ru).

## What is in this repository

- **Hardened generator facade** — fail-closed index policy, canonical templates, HTML auto-escaping and generated-proof sanitization.
- **Legacy generator implementation** — preserved separately for compatibility while the public facade enforces safety around it.
- **Shared city morphology** — one dependency-free implementation used by rendering and repair tools.
- **SEO release audit** — robots/sitemap/canonical consistency, JSON-LD validity, internal-link checks and duplicate detection.
- **Reviewed index policy flow** — external/versioned policy data instead of silently relying on stale Python arrays.
- **Dry-run-first maintenance** — production-mutating tools require an explicit `--apply`.
- **Atomic deployment helpers** — release validation, symlink switch, rollback target reporting and safe release retention.
- **Nginx/server hardening** — canonical host, rate limiting, security headers, conservative log/disk cleanup.
- **Standalone tests and validation** — the same validation entrypoint is used locally and by GitHub Actions.

## Architecture note

This is the public tooling layer, **not the complete backend application**. Some scripts still integrate with a private `app.*` package (`app.core.config`, models and services) deployed with x-gu.ru and therefore cannot perform full production rendering from a fresh public clone alone.

The generator is split deliberately:

- `content_generator.py` — hardened facade;
- `_content_generator_legacy.py` — preserved legacy implementation;
- `city_morphology.py` — shared city-name morphology.

Deploy those three files as a set. `server-opt/install_generator_facade.py` exists specifically to stage, back up and install them together with rollback on a partial failure.

The repository currently keeps compatibility copies of the three master Jinja templates at the root and under `server-opt/templates/`. They must remain byte-identical; repository validation fails if they diverge. The hardened generator prefers the canonical `server-opt/templates` set and uses legacy `app/templates` only as a compatibility fallback.

## Project structure

```text
.
├── .github/workflows/ci.yml
├── .env.example
├── scripts/
│   ├── validate_repo.py              # one-command local/CI validation
│   └── repo_healthcheck.py           # repository architecture invariants
├── tests/                             # standalone unittest regression suite
├── content_generator.py              # hardened generator facade
├── _content_generator_legacy.py      # preserved implementation
├── city_morphology.py                # shared Russian city morphology
├── seo_healthcheck.py                 # offline deployed/release SEO audit
├── seo_inplace_fix.py                 # dry-run repair; --apply to write
├── seo_rebuild_broken.py              # dry-run targeted rebuild
├── seo_title_extend.py                # dry-run title repair
├── *_master.html.j2                   # compatibility template copies
└── server-opt/
    ├── templates/                     # canonical production templates
    ├── nginx/                         # canonical Nginx configuration
    ├── index_policy.example.json      # schema/example; intentionally non-production
    ├── index_policy.baseline.json     # historical reviewed baseline data
    ├── shrink_index.py                # index-core plan/apply tool
    ├── sanitize_generated_proof.py    # existing-page proof cleanup
    ├── install_generator_facade.py    # transactional private-backend installer
    ├── deploy_release.py              # atomic current-symlink switch
    ├── prune_releases.py              # active-safe release retention
    ├── verify_whitelist.py            # protected URL acceptance check
    └── ...
```

## Requirements

- Python **3.11+**
- Ruff for repository validation
- private `app.*` package only for backend-integrated commands
- production data/config files for full rendering and index-management jobs

Basic setup:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip install ruff
cp .env.example .env
```

Never commit a populated `.env` or real credentials.

## Validate the repository

Use the same entrypoint locally that CI uses:

```bash
python scripts/validate_repo.py
```

It runs:

1. Python syntax compilation;
2. Ruff fatal-error checks (`E9`, `F63`, `F7`, `F82`);
3. all standalone `unittest` tests;
4. repository architecture/safety invariants.

For a constrained environment where Ruff is deliberately unavailable, a **limited** validation is possible with:

```bash
python scripts/validate_repo.py --skip-ruff
```

That flag is not used by CI.

The test suite currently covers city morphology, index-policy loading, robots/sitemap logic, malformed JSON-LD, broken internal links, duplicate canonical URLs, atomic release switching, release pruning and transactional generator installation/rollback.

## Hardened generator behavior

The facade enforces these safeguards around the legacy implementation:

- HTML Jinja auto-escaping is enabled;
- canonical repository templates have priority over stale compatibility copies;
- a missing `data/index_keep_config.json` is a hard error instead of silently treating every generated page as indexable;
- the only missing-config escape hatch is explicit `XGU_ALLOW_MISSING_KEEP_CONFIG=1` for controlled migration/local work;
- rendering and repair tools use the same city morphology implementation;
- generated `Review` / `AggregateRating`, generated city `LocalBusiness`, synthetic review sections, hash-derived city KPI blocks and unsupported blanket proof claims are removed/neutralized before generated HTML leaves the facade.

The legacy file still contains old generation logic for compatibility/reviewability. The hardened output path, not the existence of that legacy source text, is the production safety boundary.

## SEO healthcheck

`seo_healthcheck.py` can run without importing the private backend at module load. It audits generated/deployed static HTML for:

- title, description, H1, language and OG metadata;
- title/description lengths;
- canonical domain and canonical-to-page mismatch;
- duplicate title, H1 and canonical groups;
- valid JSON-LD;
- broken internal page links;
- expected-open pages accidentally carrying `noindex`;
- expected-closed pages accidentally indexable;
- open pages missing from sitemap;
- closed pages leaking into sitemap.

The total count of `noindex` pages is informational. It is **not** treated as an error by itself because x-gu.ru intentionally keeps a reduced indexable core.

Important environment variables are documented in `.env.example`, including:

```text
SEOHC_ROOT
SEOHC_BASE_URL
SEOHC_KEEP_CONFIG
SEOHC_WHITELIST
```

## Index-core policy

Normal production index changes require a reviewed JSON policy. The default path is:

```text
/opt/p3-app/data/index_policy.json
```

or set `XGU_INDEX_POLICY` / pass `--policy` explicitly.

`server-opt/index_policy.example.json` is intentionally marked `example_only=true`; `shrink_index.py` refuses to use it as a production policy. Copy it, replace its values with reviewed data, record review/source metadata and remove the example-only marker before use.

Dry-run a reviewed policy:

```bash
python server-opt/shrink_index.py --policy /opt/p3-app/data/index_policy.json
```

Apply only after reviewing the plan:

```bash
python server-opt/shrink_index.py --policy /opt/p3-app/data/index_policy.json --apply
```

The applied `index_keep_config.json` records the policy source and SHA-256 digest for auditability. A missing whitelist is a hard error. Sitemap/keep-config are not rewritten after a failed page-application phase.

`server-opt/index_policy.baseline.json` preserves the historical 2026-08-17 baseline as versioned data. It is an emergency/recovery reference, not a claim that the list is still optimal today. Using it requires the explicit `--use-builtin-policy` flag.

## Dry-run-first production commands

Commands that can mutate deployed content require explicit `--apply`. Examples:

```bash
python seo_inplace_fix.py
python seo_title_extend.py
python seo_rebuild_broken.py
python server-opt/sanitize_generated_proof.py
python server-opt/shrink_index.py --policy /path/to/reviewed-policy.json
python server-opt/rerender_open_hubs.py
```

The commands above only inspect/plan unless `--apply` is supplied.

## Install the hardened generator into the private backend

Preview the three-file install set:

```bash
python server-opt/install_generator_facade.py
```

Apply after review:

```bash
python server-opt/install_generator_facade.py --apply
```

The installer stages all files before touching live targets, creates backups, and restores already-replaced files automatically if a later replacement fails.

After installation, do a **single-page render smoke test** before any bulk re-render.

## Release deployment and rollback

The preferred deployment model is:

```text
build -> validate -> release directory -> atomic current symlink switch
```

A release directory must at least contain valid-looking, non-empty:

- `index.html`
- `robots.txt`
- `sitemap.xml`

Preview a release switch:

```bash
python server-opt/deploy_release.py /var/www/x-gu.ru/releases/<release-id>
```

Apply it:

```bash
python server-opt/deploy_release.py /var/www/x-gu.ru/releases/<release-id> --apply
```

The helper refuses to replace a real `current` directory/file: `current` must already be a symlink. The switch itself uses an atomic symlink replacement and prints the previous release as the rollback target.

Old releases can be reviewed for pruning with:

```bash
python server-opt/prune_releases.py --keep 5
```

and removed only with explicit `--apply`. The active release is always protected and deletion is constrained to direct child directories of the releases root.

## Existing synthetic-proof cleanup

New renders are sanitized by the generator facade. Existing deployed pages can be inspected separately:

```bash
python server-opt/sanitize_generated_proof.py
```

Apply only after checking the candidate count:

```bash
python server-opt/sanitize_generated_proof.py --apply
```

## Nginx deployment

`server-opt/nginx/x-gu.ru.conf` is the only canonical vhost file kept in the repository. Historical `.current` / `.new` copies are deliberately removed; Git history is the configuration history.

The vhost includes:

- canonical `www.x-gu.ru -> https://x-gu.ru` redirects;
- HTTP -> HTTPS canonical redirect;
- `server_tokens off` in the global config;
- baseline browser security headers;
- rate limiting and small body limit for `/api/v1/leads/submit`;
- 429 for rate-limit rejection;
- long-lived static asset caching.

Deploy `nginx.conf` and `x-gu.ru.conf` as a pair and always validate before reload:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

`/console` remains proxied to the private backend. The backend must enforce authentication/authorization, or access must be restricted at Nginx/VPN/IP level before production exposure.

## Disk/log safety

The cleanup scripts preserve active authentication/application logs and useful rollback history. Emergency disk cleanup is configurable instead of hard-wiring an extremely short journal window. Because the server is disk-constrained, release retention should be used rather than accumulating unlimited release directories.

Increasing server disk capacity is still preferable to increasingly aggressive deletion of operational history.

## Security

- Never commit secrets, OAuth credentials, API tokens or real `.env` files.
- Nginx rate limiting complements, but does not replace, backend payload validation and anti-spam controls.
- Privileged backend routes must enforce authentication/authorization independently of static-site protections.
- Production mutation should go through reviewed policy/config, dry-run output and explicit `--apply`.
- Security-sensitive changes should be merged only after the validation suite has actually executed successfully.

## License

MIT License — see [LICENSE](LICENSE).
