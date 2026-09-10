#!/usr/bin/env python3
"""Static repository safety invariants for x-gu.ru.

The checker deliberately avoids importing the private ``app.*`` backend. It is
run by ``scripts/validate_repo.py`` after syntax/lint/unit checks and protects
architecture decisions that must not silently regress.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TEMPLATES = (
    "landing_master.html.j2",
    "city_hub_master.html.j2",
    "homepage_master.html.j2",
)

PRODUCTION_MUTATORS = (
    "seo_inplace_fix.py",
    "seo_title_extend.py",
    "seo_rebuild_broken.py",
    "server-opt/shrink_index.py",
    "server-opt/purge_closed_pages.py",
    "server-opt/rerender_hubs_home.py",
    "server-opt/rerender_open_hubs.py",
    "server-opt/inject_chat_widget.py",
    "server-opt/swap_tailwind_cdn.py",
    "server-opt/patch_landing_fixes.py",
    "server-opt/sanitize_generated_proof.py",
    "server-opt/deploy_release.py",
    "server-opt/prune_releases.py",
    "server-opt/install_generator_facade.py",
    "server-opt/bootstrap_release_layout.py",
)

RELEASE_FIRST_MUTATORS = PRODUCTION_MUTATORS[:11]

BACKEND_GENERATOR_CONSUMERS = (
    "seo_rebuild_broken.py",
    "server-opt/rerender_hubs_home.py",
    "server-opt/rerender_open_hubs.py",
    "server-opt/sanitize_generated_proof.py",
)

TEST_FILES = (
    "tests/test_city_morphology.py",
    "tests/test_seo_healthcheck.py",
    "tests/test_deploy_release.py",
    "tests/test_prune_releases.py",
    "tests/test_install_generator_facade.py",
    "tests/test_shrink_index_policy.py",
    "tests/test_predeploy_check.py",
    "tests/test_release_safety.py",
    "tests/test_purge_closed_pages.py",
    "tests/test_bootstrap_release_layout.py",
)


def read(rel: str, failures: list[str]) -> str:
    path = ROOT / rel
    if not path.is_file():
        failures.append(f"missing file: {rel}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        failures.append(f"cannot read {rel}: {exc}")
        return ""


def need(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def tokens(text: str, required: dict[str, str], failures: list[str]) -> None:
    for token, message in required.items():
        need(token in text, message, failures)


def check_templates(failures: list[str]) -> None:
    for name in TEMPLATES:
        root = ROOT / name
        canonical = ROOT / "server-opt" / "templates" / name
        need(root.is_file(), f"missing root template: {name}", failures)
        need(canonical.is_file(), f"missing canonical template: {name}", failures)
        if root.is_file() and canonical.is_file():
            need(root.read_bytes() == canonical.read_bytes(), f"template copies diverged: {name}", failures)

    landing = read("server-opt/templates/landing_master.html.j2", failures)
    hub = read("server-opt/templates/city_hub_master.html.j2", failures)
    for name in TEMPLATES:
        text = read(f"server-opt/templates/{name}", failures)
        need("cdn.tailwindcss.com" not in text, f"{name}: Tailwind Play CDN returned", failures)
        need("avitobibot" not in text, f"{name}: obsolete Telegram handle returned", failures)
    need("{{ robots_content" in hub, "city hub ignores robots_content", failures)
    need("/privacy/" in landing, "landing has no privacy link", failures)
    need("/assets/tailwind.min.css" in landing, "landing does not use local Tailwind CSS", failures)


def check_generator(failures: list[str]) -> None:
    facade = read("content_generator.py", failures)
    read("_content_generator_legacy.py", failures)
    read("city_morphology.py", failures)
    tokens(
        facade,
        {
            "autoescape=True": "generator autoescape is not enforced",
            '_RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"': "release policy manifest support missing",
            '_RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"': "release whitelist support missing",
            "XGU_ALLOW_LEGACY_KEEP_CONFIG": "legacy keep-config is not explicitly gated",
            "XGU_ALLOW_LEGACY_WHITELIST": "legacy whitelist is not explicitly gated",
            "XGU_ALLOW_MISSING_KEEP_CONFIG": "missing-policy migration gate missing",
            "Release whitelist SHA-256 mismatch": "generator does not verify release whitelist hash",
            '_legacy._landing_variants = _safe_landing_variants': "legacy testimonial cards are not disabled before render",
            '_legacy._review_variant = _disabled_review_variant': "legacy deterministic review object is not disabled",
            '_legacy._city_prepositional = city_prepositional': "legacy generator does not use shared city morphology",
            "_sanitize_generated_html": "generated HTML sanitizer missing",
            'key in {"aggregateRating", "review"}': "review/rating JSON-LD sanitizer missing",
            'payload.get("@type") == "LocalBusiness"': "generated LocalBusiness sanitizer missing",
        },
        failures,
    )
    need("reviewCount" not in facade, "synthetic review count leaked into hardened facade", failures)

    canonical = facade.find('candidates.append(root / "server-opt" / "templates")')
    legacy = facade.find('candidates.append(Path("app/templates"))')
    need(canonical >= 0 and legacy > canonical, "canonical templates are not preferred over app/templates", failures)


def check_policy_and_seo(failures: list[str]) -> None:
    shrink = read("server-opt/shrink_index.py", failures)
    health = read("seo_healthcheck.py", failures)
    predeploy = read("server-opt/predeploy_check.py", failures)

    tokens(
        shrink,
        {
            'RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"': "shrink_index does not write release policy",
            'RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"': "shrink_index does not snapshot whitelist",
            "--use-builtin-policy": "emergency baseline is not explicitly gated",
            "example_only": "example policy is not rejected",
            "policy_sha256": "policy digest provenance missing",
            "whitelist_sha256": "whitelist digest provenance missing",
            "mutation_target_error": "shrink_index is not release-target guarded",
            "atomic_replace_text": "shrink_index bypasses atomic writes",
        },
        failures,
    )
    need("BUILTIN_OPEN_CITIES" not in shrink, "city policy leaked back into Python", failures)
    need("BUILTIN_OPEN_SERVICES" not in shrink, "service policy leaked back into Python", failures)
    need("/opt/p3-app/data/index_keep_config.json" not in shrink, "shrink_index uses legacy global keep-config", failures)

    for rel in ("server-opt/index_policy.example.json", "server-opt/index_policy.baseline.json"):
        text = read(rel, failures)
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            failures.append(f"invalid policy JSON {rel}: {exc}")
            continue
        if rel.endswith("example.json"):
            need(payload.get("example_only") is True, "example policy is not marked example_only", failures)
        else:
            need(bool(payload.get("reviewed_at")), "baseline policy has no reviewed_at", failures)
            need(bool(payload.get("source_note")), "baseline policy has no source_note", failures)
            need(bool(payload.get("open_cities")), "baseline policy has no cities", failures)
            need(bool(payload.get("open_services")), "baseline policy has no services", failures)

    tokens(
        health,
        {
            "RELEASE_KEEP_FILENAME": "SEO healthcheck is not release-policy aware",
            "RELEASE_WHITELIST_FILENAME": "SEO healthcheck is not release-whitelist aware",
            "SEOHC_REQUIRE_POLICY": "SEO healthcheck is not fail-closed on missing policy",
            "release whitelist SHA-256 mismatch": "SEO healthcheck does not verify whitelist hash",
            "unexpected_noindex_open": "SEO healthcheck cannot detect wrong noindex",
            "unexpected_index_closed": "SEO healthcheck cannot detect wrongly indexed closed pages",
            "sitemap_orphan_urls": "SEO healthcheck cannot detect sitemap orphan URLs",
            "canonical_duplicate_pages": "SEO healthcheck cannot detect duplicate canonicals",
            "invalid_jsonld_pages": "SEO healthcheck cannot detect invalid JSON-LD",
            "broken_internal_links": "SEO healthcheck cannot detect broken internal links",
        },
        failures,
    )

    tokens(
        predeploy,
        {
            'KEEP_FILENAME = ".xgu-index-keep.json"': "predeploy is not bound to release policy",
            'WHITELIST_FILENAME = ".xgu-whitelist.txt"': "predeploy is not bound to release whitelist",
            "release whitelist SHA-256 mismatch": "predeploy does not verify whitelist hash",
            'audit.get("policy_loaded")': "predeploy does not prove policy load",
            "evaluate(audit)": "predeploy does not execute strict SEO thresholds",
        },
        failures,
    )


def check_mutation_safety(failures: list[str]) -> None:
    for rel in PRODUCTION_MUTATORS:
        text = read(rel, failures)
        if text:
            need("--apply" in text, f"{rel}: real writes are not gated by --apply", failures)

    for rel in RELEASE_FIRST_MUTATORS:
        text = read(rel, failures)
        if not text:
            continue
        need("mutation_target_error" in text, f"{rel}: release target guard missing", failures)
        need("--unsafe-allow-active-current" in text, f"{rel}: active override is not explicit", failures)
        need(".write_text(" not in text, f"{rel}: direct text write bypasses atomic helper", failures)

    for rel in BACKEND_GENERATOR_CONSUMERS:
        text = read(rel, failures)
        need("from app.services.content_generator import" in text, f"{rel}: installed generator import missing", failures)
        need("from content_generator import" not in text, f"{rel}: stale top-level generator import returned", failures)

    hubs = read("server-opt/rerender_hubs_home.py", failures)
    need("cities_for_home" in hubs and "open_cities" in hubs, "full rerender can repopulate closed homepage cities", failures)
    need("allowed_slugs" in hubs and "open_services" in hubs, "full rerender can expose closed services", failures)

    purge = read("server-opt/purge_closed_pages.py", failures)
    tokens(
        purge,
        {
            'WHITELIST_FILENAME = ".xgu-whitelist.txt"': "purge does not use release whitelist",
            "release whitelist SHA-256 mismatch": "purge does not verify whitelist hash",
            "if not index_file.is_file():": "purge can delete directory without index.html",
            "if not _explicit_noindex(index_file):": "purge can delete page without confirmed noindex",
        },
        failures,
    )
    need(purge.count("mutation_target_error(") >= 2, "purge does not re-check active release per delete", failures)


def check_release_control_plane(failures: list[str]) -> None:
    safety = read("release_safety.py", failures)
    deploy = read("server-opt/deploy_release.py", failures)
    bootstrap = read("server-opt/bootstrap_release_layout.py", failures)
    prune = read("server-opt/prune_releases.py", failures)
    installer = read("server-opt/install_generator_facade.py", failures)

    tokens(
        safety,
        {
            "def mutation_target_error(": "release mutation guard missing",
            "def atomic_replace_text(": "atomic write helper missing",
            "os.replace(temp, path)": "atomic write helper no longer uses os.replace",
            "def release_operation_lock(": "host-wide release operation lock missing",
            "fcntl.LOCK_EX | fcntl.LOCK_NB": "release lock is not exclusive/non-blocking",
            "another release operation already holds lock": "concurrent release operation is not rejected",
        },
        failures,
    )

    for label, text in (("deploy", deploy), ("bootstrap", bootstrap), ("prune", prune)):
        need("release_operation_lock" in text, f"{label}: host-wide release lock missing", failures)
        need("--lock-file" in text, f"{label}: lock path is not explicit/configurable", failures)

    tokens(
        deploy,
        {
            "run_predeploy(": "deploy does not run strict predeploy",
            "os.replace(temp_link, current)": "deploy current switch is not atomic",
            "rollback target": "deploy does not report rollback target",
            "Re-run every gate while the production release lock is held": "deploy does not revalidate under lock",
        },
        failures,
    )

    tokens(
        bootstrap,
        {
            "target release is missing required non-empty file": "bootstrap does not require self-contained candidate",
            "run_predeploy(": "bootstrap does not predeploy target",
            "os.rename(current, backup_release)": "bootstrap does not archive legacy current",
            "os.replace(temp_link, current)": "bootstrap cutover is not atomic",
            "automatic rollback also failed": "bootstrap catastrophic rollback path missing",
            "Re-run layout + strict predeploy": "bootstrap does not revalidate under lock",
        },
        failures,
    )

    tokens(
        prune,
        {
            'BOOTSTRAP_BACKUP_PREFIX = "pre-bootstrap-"': "prune does not identify bootstrap backup",
            "--include-bootstrap-backups": "bootstrap backup deletion has no explicit opt-in",
            "protect_bootstrap_backups": "prune does not protect bootstrap backups by default",
            "Rebuild the plan only after acquiring": "prune can apply a stale pre-lock deletion plan",
            "_delete_if_still_inactive": "prune does not re-check active release before deletion",
        },
        failures,
    )

    tokens(
        installer,
        {
            'compile(source, str(path), "exec")': "generator installer does not syntax-check source",
            "Stage every new file before mutating any live target": "generator installer does not stage complete set",
            "for target in reversed(replaced)": "generator installer rollback loop missing",
            "os.replace(restore_temp, target)": "generator installer rollback is not atomic",
        },
        failures,
    )


def check_validation_and_docs(failures: list[str]) -> None:
    ci = read(".github/workflows/ci.yml", failures)
    validator = read("scripts/validate_repo.py", failures)
    env = read(".env.example", failures)
    readme = read("README.md", failures)
    runbook = read("server-opt/PRODUCTION_DEPLOY_RUNBOOK.md", failures)

    for rel in TEST_FILES:
        read(rel, failures)

    tokens(
        validator,
        {
            '"-m", "compileall"': "validator lost compileall",
            'shutil.which("ruff")': "validator does not locate Ruff CLI",
            '"E9,F63,F7,F82"': "validator lost fatal Ruff rules",
            '"-m", "unittest"': "validator lost unit tests",
            "scripts/repo_healthcheck.py": "validator lost repository healthcheck",
        },
        failures,
    )
    need("python scripts/validate_repo.py" in ci, "CI does not use shared validator", failures)
    need("python -m pip install --disable-pip-version-check ruff" in ci, "CI does not install Ruff", failures)

    need("SEOHC_REQUIRE_POLICY=true" in env, ".env.example does not document fail-closed SEO policy", failures)
    need("XGU_ALLOW_LEGACY_WHITELIST" in env, ".env.example omits legacy whitelist gate", failures)
    need("PRODUCTION_DEPLOY_RUNBOOK.md" in readme, "README does not link production runbook", failures)
    need("bootstrap_release_layout.py \"$RELEASE\"" in runbook, "runbook has no guarded first bootstrap", failures)
    need("pre-bootstrap-*" in runbook, "runbook does not preserve legacy bootstrap backup", failures)
    need("python scripts/validate_repo.py" in runbook, "runbook skips repository validation", failures)


def check_nginx_and_cleanup(failures: list[str]) -> None:
    nginx_dir = ROOT / "server-opt" / "nginx"
    need(not (nginx_dir / "x-gu.ru.conf.current").exists(), "stale Nginx .current copy returned", failures)
    need(not (nginx_dir / "x-gu.ru.conf.new").exists(), "stale Nginx .new copy returned", failures)

    nginx = read("server-opt/nginx/nginx.conf", failures)
    vhost = read("server-opt/nginx/x-gu.ru.conf", failures)
    tokens(
        nginx,
        {
            "server_tokens off;": "Nginx exposes server version",
            "zone=lead_submit": "lead rate-limit zone missing",
        },
        failures,
    )
    tokens(
        vhost,
        {
            "location = /api/v1/leads/submit": "lead endpoint exact location missing",
            "limit_req_status 429;": "rate limiting does not return 429",
            "return 301 https://x-gu.ru$request_uri;": "canonical HTTP/www redirect missing",
            'X-Content-Type-Options "nosniff"': "baseline security headers missing",
        },
        failures,
    )

    cleanup = read("server-opt/cleanup_junk.sh", failures)
    disk = read("server-opt/disk-autoclean.sh", failures)
    need(": > /var/log/auth.log" not in cleanup, "cleanup truncates auth.log", failures)
    need("auth.log" not in disk, "disk autoclean directly targets auth logs", failures)
    need("JOURNAL_DAYS" in disk and "JOURNAL_MAX" in disk, "journal retention is not configurable", failures)


def main() -> int:
    failures: list[str] = []
    check_templates(failures)
    check_generator(failures)
    check_policy_and_seo(failures)
    check_mutation_safety(failures)
    check_release_control_plane(failures)
    check_validation_and_docs(failures)
    check_nginx_and_cleanup(failures)

    if failures:
        print("Repository healthcheck: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("Repository healthcheck: OK")
    print("  templates are synchronized and canonical")
    print("  fabricated testimonials are disabled before render and sanitized after render")
    print("  policy + whitelist are release-bound and fail-closed")
    print("  bulk mutations are candidate-only and atomic")
    print("  deploy/bootstrap/prune share a non-blocking host-wide lock")
    print("  bootstrap legacy backups are protected from normal pruning")
    print("  strict SEO/predeploy/deploy invariants are present")
    print("  CI/local validation use one entrypoint")
    print("  Nginx and cleanup safety invariants are present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
