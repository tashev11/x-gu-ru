#!/usr/bin/env python3
"""Static repository safety and programmatic-SEO invariants for x-gu.ru."""
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
    "tests/test_index_policy.py",
    "tests/test_seo_healthcheck.py",
    "tests/test_programmatic_seo_audit.py",
    "tests/test_build_search_evidence.py",
    "tests/test_build_pair_policy.py",
    "tests/test_deploy_release.py",
    "tests/test_prune_releases.py",
    "tests/test_install_generator_facade.py",
    "tests/test_shrink_index_policy.py",
    "tests/test_predeploy_check.py",
    "tests/test_release_safety.py",
    "tests/test_release_integrity.py",
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


def check_policy_model(failures: list[str]) -> None:
    policy = read("index_policy.py", failures)
    tokens(
        policy,
        {
            "SUPPORTED_POLICY_VERSIONS = {1, 2}": "shared policy model does not support v1+v2",
            'mode = "matrix"': "historical matrix policy compatibility missing",
            'mode = "pairs"': "pair-level policy mode missing",
            "open_pairs": "pair-level policy has no explicit pairs",
            "references city hub not present in open_cities": "v2 allows service pair under a closed city hub",
            "def services_for_city(": "city-specific service resolver missing",
            "def keep_urls(": "shared policy cannot build exact indexable URL set",
            "sorted(f\"{city}/{service}\"": "policy digest is not canonicalized across pair ordering",
        },
        failures,
    )

    example_text = read("server-opt/index_policy.example.json", failures)
    baseline_text = read("server-opt/index_policy.baseline.json", failures)
    try:
        example = json.loads(example_text) if example_text else {}
        baseline = json.loads(baseline_text) if baseline_text else {}
    except json.JSONDecodeError as exc:
        failures.append(f"index policy JSON invalid: {exc}")
        return
    need(example.get("policy_version") == 2, "policy example is not pair-level v2", failures)
    need(example.get("example_only") is True, "policy example is not protected as example_only", failures)
    need(bool(example.get("open_pairs")), "policy v2 example has no open_pairs", failures)
    need(baseline.get("policy_version") == 1, "historical baseline no longer identifies itself as v1", failures)
    need(bool(baseline.get("reviewed_at")), "baseline policy has no reviewed_at", failures)
    need(bool(baseline.get("source_note")), "baseline policy has no source_note", failures)


def check_generator(failures: list[str]) -> None:
    facade = read("content_generator.py", failures)
    read("_content_generator_legacy.py", failures)
    read("city_morphology.py", failures)
    tokens(
        facade,
        {
            "autoescape=True": "generator autoescape is not enforced",
            "normalize_policy_payload": "generator does not parse shared v1/v2 policy",
            "policy_page_is_open": "generator does not use exact shared indexability decision",
            '_RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"': "release policy manifest support missing",
            '_RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"': "release whitelist support missing",
            "XGU_ALLOW_LEGACY_KEEP_CONFIG": "legacy keep-config is not explicitly gated",
            "XGU_ALLOW_LEGACY_WHITELIST": "legacy whitelist is not explicitly gated",
            "XGU_ALLOW_MISSING_KEEP_CONFIG": "missing-policy migration gate missing",
            "Release whitelist SHA-256 mismatch": "generator does not verify release whitelist hash",
            '_legacy._landing_variants = _safe_landing_variants': "legacy testimonials are not disabled before render",
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

    installer = read("server-opt/install_generator_facade.py", failures)
    need('"index_policy.py"' in installer, "generator installer does not deploy shared index policy", failures)


def check_policy_and_seo(failures: list[str]) -> None:
    shrink = read("server-opt/shrink_index.py", failures)
    health = read("seo_healthcheck.py", failures)
    predeploy = read("server-opt/predeploy_check.py", failures)
    corpus = read("server-opt/programmatic_seo_audit.py", failures)
    evidence = read("server-opt/build_search_evidence.py", failures)
    pair_builder = read("server-opt/build_pair_policy.py", failures)

    tokens(
        shrink,
        {
            "load_policy_model": "shrink_index does not load normalized v1/v2 policy",
            "build_keep_urls_for_policy": "shrink_index still assumes a blind city/service cross-product",
            "manifest_policy_fields": "release manifest does not preserve policy version/pairs",
            'RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"': "shrink_index does not write release policy",
            'RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"': "shrink_index does not snapshot whitelist",
            "--use-builtin-policy": "emergency baseline is not explicitly gated",
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

    tokens(
        health,
        {
            "normalize_policy_payload": "SEO healthcheck does not parse v2 policy",
            "policy_page_is_open": "SEO healthcheck does not validate exact city/service pair indexability",
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
        corpus,
        {
            "physical_pages": "full-corpus SEO audit does not report physical inventory",
            "indexable_pages": "full-corpus SEO audit does not report indexable inventory",
            "orphan_indexable_pages": "full-corpus SEO audit does not detect orphan indexable pages",
            "indexable_links_to_closed": "full-corpus SEO audit does not detect open-to-closed links",
            "near_duplicate_pages": "full-corpus SEO audit does not detect near duplicates",
            "_near_duplicate_groups": "full-corpus SEO audit lost near-duplicate grouping",
        },
        failures,
    )
    tokens(
        evidence,
        {
            "fetch_yandex_urls": "search evidence does not collect Yandex URLs",
            "fetch_gsc_pages": "search evidence does not collect GSC pages",
            '"startRow"': "GSC evidence is not paginated",
            "candidate protected URLs": "search evidence no longer produces a review candidate",
            "Source /opt/p3-app/data/whitelist.txt was NOT changed": "evidence builder may silently promote whitelist",
        },
        failures,
    )
    tokens(
        pair_builder,
        {
            '"policy_version": 2': "pair-policy builder does not emit v2",
            '"example_only": True': "pair-policy candidate is not protected from direct production use",
            '"open_pairs"': "pair-policy builder does not emit exact city/service pairs",
            "below threshold": "pair-policy builder does not expose weak-signal exclusions",
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

    for rel in ("seo_rebuild_broken.py", "server-opt/rerender_hubs_home.py", "server-opt/rerender_open_hubs.py"):
        text = read(rel, failures)
        need("services_for_city" in text, f"{rel}: city hub still exposes a global service matrix", failures)
        need("normalize_policy_payload" in text, f"{rel}: does not validate v1/v2 release policy", failures)

    hubs = read("server-opt/rerender_hubs_home.py", failures)
    need("cities_for_home" in hubs and "open_cities" in hubs, "full rerender can repopulate closed homepage cities", failures)

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
    integrity = read("release_integrity.py", failures)
    finalizer = read("server-opt/finalize_release.py", failures)

    tokens(
        safety,
        {
            "def mutation_target_error(": "release mutation guard missing",
            "def atomic_replace_text(": "atomic write helper missing",
            "def release_operation_lock(": "host-wide release operation lock missing",
            "fcntl.LOCK_EX | fcntl.LOCK_NB": "release lock is not exclusive/non-blocking",
            "another release operation already holds lock": "concurrent release operation is not rejected",
        },
        failures,
    )
    for label, text in (("deploy", deploy), ("bootstrap", bootstrap), ("prune", prune)):
        need("release_operation_lock" in text, f"{label}: host-wide release lock missing", failures)
        need("--lock-file" in text, f"{label}: lock path is not explicit/configurable", failures)

    need("run_predeploy(" in deploy, "deploy does not run strict predeploy", failures)
    need("rollback target" in deploy, "deploy does not report rollback target", failures)
    need("run_predeploy(" in bootstrap, "bootstrap does not predeploy target", failures)
    need("pre-bootstrap-" in prune, "prune does not protect bootstrap backup class", failures)
    need('compile(source, str(path), "exec")' in installer, "generator installer does not syntax-check source", failures)
    need("compute_release_fingerprint" in integrity, "release integrity fingerprint implementation missing", failures)
    need("verify_release_manifest" in deploy, "deploy does not verify finalized release fingerprint", failures)
    need("write_release_manifest" in finalizer, "release finalizer does not write immutable manifest", failures)


def check_validation_and_docs(failures: list[str]) -> None:
    ci = read(".github/workflows/ci.yml", failures)
    validator = read("scripts/validate_repo.py", failures)
    env = read(".env.example", failures)
    readme = read("README.md", failures)
    seo_arch = read("SEO_ARCHITECTURE.md", failures)
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
    need("SEOHC_REQUIRE_POLICY=true" in env, ".env.example does not document fail-closed SEO policy", failures)
    need("PRODUCTION_DEPLOY_RUNBOOK.md" in readme, "README does not link production runbook", failures)
    need("open_pairs" in seo_arch, "SEO architecture does not document pair-level target model", failures)
    need("programmatic_seo_audit.py" in seo_arch, "SEO architecture does not document full-corpus audit", failures)
    need("build_search_evidence.py" in seo_arch, "SEO architecture does not document combined search evidence", failures)
    need("build_pair_policy.py" in seo_arch, "SEO architecture does not document v2 candidate builder", failures)
    need("python scripts/validate_repo.py" in runbook, "runbook skips repository validation", failures)


def check_nginx_and_cleanup(failures: list[str]) -> None:
    nginx_dir = ROOT / "server-opt" / "nginx"
    need(not (nginx_dir / "x-gu.ru.conf.current").exists(), "stale Nginx .current copy returned", failures)
    need(not (nginx_dir / "x-gu.ru.conf.new").exists(), "stale Nginx .new copy returned", failures)
    nginx = read("server-opt/nginx/nginx.conf", failures)
    vhost = read("server-opt/nginx/x-gu.ru.conf", failures)
    need("server_tokens off;" in nginx, "Nginx exposes server version", failures)
    need("zone=lead_submit" in nginx, "lead rate-limit zone missing", failures)
    need("location = /api/v1/leads/submit" in vhost, "lead endpoint exact location missing", failures)
    need("limit_req_status 429;" in vhost, "rate limiting does not return 429", failures)
    need("return 301 https://x-gu.ru$request_uri;" in vhost, "canonical HTTP/www redirect missing", failures)

    cleanup = read("server-opt/cleanup_junk.sh", failures)
    disk = read("server-opt/disk-autoclean.sh", failures)
    need(": > /var/log/auth.log" not in cleanup, "cleanup truncates auth.log", failures)
    need("auth.log" not in disk, "disk autoclean directly targets auth logs", failures)


def main() -> int:
    failures: list[str] = []
    check_templates(failures)
    check_policy_model(failures)
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
    print("  SEO policy v1 matrix + v2 exact pairs share one model")
    print("  generator, sitemap, healthcheck and hub links use the same indexability rules")
    print("  full-corpus SEO audit and combined Yandex/GSC evidence tooling are present")
    print("  pair-policy candidates are review-only and cannot auto-promote to production")
    print("  bulk mutations are candidate-only and atomic")
    print("  finalized releases are fingerprinted and release control operations are serialized")
    print("  CI/local validation use one entrypoint")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
