# X-GU.RU — Content & SEO Tooling

[![CI](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml/badge.svg)](https://github.com/tashev11/x-gu-ru/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)

Content generation, SEO auditing, index-management and production support tooling used around [x-gu.ru](https://x-gu.ru).

## What is in this repository

- **Hardened content generator facade** — fail-safe entry point for Jinja rendering and index policy.
- **Legacy generator implementation** — preserved separately for compatibility and reviewability.
- **SEO health checks** — title/description/canonical/H1/JSON-LD/indexability checks.
- **Index-core tooling** — sitemap, whitelist, Yandex Webmaster and Google Search Console utilities.
- **Production maintenance** — Nginx, fail2ban, disk/zram and carefully scoped repair scripts.
- **Repository safety checks** — CI verifies syntax, fatal Python errors and critical template/Nginx/maintenance invariants.

## Important architecture note

This repository is the public tooling layer, **not the complete backend application**. Several scripts import a private `app.*` package (`app.core.config`, models and services) that is deployed with x-gu.ru but is not published here. Scripts that require those imports will not run standalone after a fresh clone.

`content_generator.py` is now the hardened public facade. The previous implementation is preserved as `_content_generator_legacy.py`; existing rendering functions are re-exported through the facade and the legacy module is patched to use the hardened template/index/render hooks. Deploy these two files together.

The repository intentionally keeps a root copy of each master template and a production copy under `server-opt/templates/`. They must remain byte-identical; CI fails if they diverge. This is a compatibility arrangement for the current deployment layout, not two independent template versions.

## Project structure

```text
.
├── .github/workflows/ci.yml       # CI safety gate
├── .env.example                   # non-secret configuration example
├── scripts/repo_healthcheck.py    # repository invariant checks
├── content_generator.py           # hardened generator facade
├── _content_generator_legacy.py   # preserved legacy implementation
├── seo_healthcheck.py             # deployed HTML SEO audit
├── seo_inplace_fix.py             # dry-run repair; --apply to write
├── seo_rebuild_broken.py          # dry-run targeted rebuild; --apply to write
├── seo_title_extend.py            # dry-run title repair; --apply to write
├── *_master.html.j2               # compatibility copies of master templates
└── server-opt/
    ├── templates/                  # production master templates
    ├── nginx/                      # canonical Nginx configuration
    ├── index_policy.example.json   # external index-core policy example
    ├── sanitize_generated_proof.py # migration for already-rendered synthetic proof
    ├── seo_report.py               # Yandex + GSC reporting
    ├── shrink_index.py             # dry-run index-core management
    ├── verify_whitelist.py         # acceptance check for protected URLs
    └── ...
```

## Requirements

- Python **3.11+**
- PostgreSQL / database configuration supplied by the private backend where required
- The private `app.*` package for backend-integrated commands
- Production data files for full rendering/index-management commands

Install the public Python dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Never put real credentials in `.env.example` or commit a populated `.env`.

## Data files

Production jobs may use files under `data/`, including:

- `keywords_all.csv`
- `keywords_wave_2.csv`
- `keywords_100.csv`
- `ru_cities_with_population.csv`
- `index_keep_config.json`
- `whitelist.txt`

The large/private production datasets are intentionally excluded from Git. Keep versioned schemas or sanitized samples separately if the data format changes.

## Hardened generator behavior

The facade enforces several production safeguards:

- Jinja HTML auto-escaping is enabled.
- A missing `data/index_keep_config.json` is a hard error instead of silently treating all generated pages as indexable. The only escape hatch is the explicit `XGU_ALLOW_MISSING_KEEP_CONFIG=1` migration/local override.
- Newly rendered HTML is sanitized to remove generated `Review` / `AggregateRating`, generated `LocalBusiness` schema, deterministic fake-review sections and hash-derived city KPI blocks.
- One complete template directory is resolved explicitly; incomplete/stale template sets are rejected.

The original large implementation remains in `_content_generator_legacy.py` so these hardening changes are small and reversible instead of rewriting the entire generator at once.

## Validation

The checks that can run without the private backend are:

```bash
python -m compileall -q .
python scripts/repo_healthcheck.py
```

GitHub Actions also runs Ruff's fatal-error rules (`E9`, `F63`, `F7`, `F82`).

The repository healthcheck protects against several costly regressions:

- root and production templates drifting apart;
- city hubs ignoring `robots_content`;
- Tailwind Play CDN returning to production templates;
- obsolete Telegram/privacy links returning;
- generator fail-closed/HTML sanitization protections disappearing;
- production-mutating maintenance commands losing their explicit `--apply` gate;
- duplicate/stale Nginx `current/new` configs;
- loss of the canonical `www -> x-gu.ru` redirect;
- loss of Nginx server-version hiding, security headers or lead rate limiting.

## Production safety rules

1. Prefer **build -> validate -> release -> switch** over editing thousands of files in `/var/www/x-gu.ru/current` in place.
2. Production-mutating utilities in this repository run as **dry-run by default**. Review their counts/plan and then repeat with `--apply`.
3. Keep recent rollback files and logs; cleanup scripts must not truncate active authentication logs.
4. Validate `nginx -t` before reloading Nginx.
5. After index-core changes, run `verify_whitelist.py` and the GSC/Yandex audit tools before deleting any closed pages.
6. Treat `/console` as privileged: the backend must enforce authentication/authorization, or Nginx must restrict it by VPN/IP.

## Index-core policy

`server-opt/shrink_index.py` can use an external JSON policy instead of editing Python lists for routine changes:

```bash
python server-opt/shrink_index.py --policy server-opt/index_policy.example.json
```

That is a **dry-run**. To apply an audited policy on the server:

```bash
python server-opt/shrink_index.py --policy /path/to/reviewed-policy.json --apply
```

A missing whitelist is a hard error, and sitemap/keep-config are not rewritten if the page-application phase reports errors.

## Existing-page cleanup

The hardened generator protects newly rendered pages. Existing deployed pages can be inspected for the same synthetic-proof cleanup with:

```bash
python server-opt/sanitize_generated_proof.py
```

If the reported candidate count is expected, apply explicitly:

```bash
python server-opt/sanitize_generated_proof.py --apply
```

## Nginx deployment

`server-opt/nginx/x-gu.ru.conf` is the **only canonical vhost file in this repository**. Historical `.current` / `.new` copies are deliberately not kept; Git history is the rollback history.

Before applying it on a server:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

The global `nginx.conf` defines the `lead_submit` rate-limit zone used by the vhost, so deploy the pair together.

## Security

- Never commit secrets, API tokens, OAuth credentials or real `.env` files.
- The public lead endpoint is rate-limited at Nginx, but the backend must still validate payloads and apply anti-spam controls.
- `/console` is proxied to the private backend and must be protected there (or restricted at Nginx).
- Security-sensitive changes should go through a branch/PR and green CI rather than direct edits to `main`.

## License

MIT License — see [LICENSE](LICENSE).
