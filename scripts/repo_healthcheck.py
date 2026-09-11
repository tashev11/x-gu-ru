#!/usr/bin/env python3
"""Static repository safety and programmatic-SEO invariants for x-gu.ru.

This checker intentionally avoids importing the private ``app.*`` backend. It
verifies that the public repository still contains the architectural safeguards
that are easy to lose during later refactors.
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
    "tests/test_index_policy.py",
    "tests/test_seo_healthcheck.py",
    "tests/test_seo_healthcheck_v2.py",
    "tests/test_programmatic_seo_audit.py",
    "tests/test_build_search_evidence.py",
    "tests/test_pair_quality_audit.py",
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


def require_tokens(text: str, required: dict[str, str], failures: list[str]) -> None:
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
    require_tokens(
        policy,
        {
            "SUPPORTED_POLICY_VERSIONS = {1, 2}": "shared policy model does not support v1 and v2",
            'mode = "matrix"': "historical v1 matrix compatibility missing",
            'mode = "pairs"': "pair-level v2 mode missing",
            "open_pairs": "pair-level policy has no explicit pairs",
            "references city hub not present in open_cities": "v2 permits a pair under a closed city hub",
            "def page_is_open(": "shared exact indexability resolver missing",
            "def services_for_city(": "city-specific service resolver missing",
            "def keep_urls(": "shared policy cannot build exact URL set",
            "def policy_digest(": "canonical policy digest missing",
            'canonical["open_pairs"] = sorted': "v2 digest is sensitive to pair ordering",
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
    need(example.get("example_only") is True, "v2 example is not protected as example_only", failures)
    need(bool(example.get("open_pairs")), "v2 example has no open_pairs", failures)
    need(baseline.get("policy_version") == 1, "historical baseline no longer identifies as v1", failures)
    need(bool(baseline.get("reviewed_at")), "baseline policy has no reviewed_at", failures)
    need(bool(baseline.get("source_note")), "baseline policy has no source_note", failures)


def check_generator(failures: list[str]) -> None:
    facade = read("content_generator.py", failures)
    installer = read("server-opt/install_generator_facade.py", failures)
    read("_content_generator_legacy.py", failures)
    read("city_morphology.py", failures)

    require_tokens(
        facade,
        {
            "autoescape=True": "generator autoescape is not enforced",
            "normalize_policy_payload": "generator does not parse shared v1/v2 policy",
            "policy_page_is_open": "generator does not use shared exact pair decision",
            '_RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"': "release policy support missing",
            '_RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"': "release whitelist support missing",
            "XGU_ALLOW_LEGACY_KEEP_CONFIG": "legacy keep-config is not explicitly gated",
            "XGU_ALLOW_LEGACY_WHITELIST": "legacy whitelist is not explicitly gated",
            "XGU_ALLOW_MISSING_KEEP_CONFIG": "missing-policy migration gate missing",
            "Release whitelist SHA-256 mismatch": "generator does not verify release whitelist hash",
            '_legacy._landing_variants = _safe_landing_variants': "legacy testimonials are not disabled before render",
            '_legacy._review_variant = _disabled_review_variant': "legacy deterministic review object is not disabled",
            '_legacy._city_prepositional = city_prepositional': "legacy generator does not use shared morphology",
            "_sanitize_generated_html": "generated HTML sanitizer missing",
            'key in {"aggregateRating", "review"}': "JSON-LD sanitizer no longer removes ratings/reviews",
            'payload.get("@type") == "LocalBusiness"': "JSON-LD sanitizer no longer removes synthetic LocalBusiness blocks",
            "_remove_reviews_section": "visible reviews section is no longer removed",
            "_remove_synthetic_counter_panel": "synthetic KPI counter panel is no longer removed",
            '"50+ проектов": "Работа по этапам"': "unsupported 50+ projects claim is no longer neutralized",
            '"TOP-10 гарантии": "Прозрачные отчёты"': "unsupported TOP-10 guarantee is no longer neutralized",
            '"24/7 поддержка": "Связь 9:00–21:00"': "unsupported 24/7 support claim is no longer neutralized",
            '"Экономия до 150 000 рублей!": "Оценим задачу и бюджет до старта."': "unsupported savings claim is no longer neutralized",
            '"Экономия до 150 000₽": "Понятный бюджет до старта"': "unsupported compact savings claim is no longer neutralized",
        },
        failures,
    )
    need("reviewCount" not in facade, "synthetic review count leaked into hardened facade", failures)

    canonical = facade.find('candidates.append(root / "server-opt" / "templates")')
    legacy = facade.find('candidates.append(Path("app/templates"))')
    need(canonical >= 0 and legacy > canonical, "canonical templates are not preferred over app/templates", failures)

    for required in (
        '"content_generator.py"',
        '"_content_generator_legacy.py"',
        '"city_morphology.py"',
        '"index_policy.py"',
    ):
        need(required in installer, f"generator installer misses required source {required}", failures)


def check_programmatic_seo(failures: list[str]) -> None:
    shrink = read("server-opt/shrink_index.py", failures)
    health = read("seo_healthcheck.py", failures)
    predeploy = read("server-opt/predeploy_check.py", failures)
    corpus = read("server-opt/programmatic_seo_audit.py", failures)
    evidence = read("server-opt/build_search_evidence.py", failures)
    quality = read("server-opt/pair_quality_audit.py", failures)
    builder = read("server-opt/build_pair_policy.py", failures)
    report = read("server-opt/seo_report.py", failures)

    require_tokens(
        shrink,
        {
            "load_policy_model": "shrink_index does not load normalized v1/v2 policy",
            "build_keep_urls_for_policy": "shrink_index still assumes a blind cross-product",
            "manifest_policy_fields": "release manifest does not preserve v2 exact pairs",
            "--use-builtin-policy": "historical v1 fallback is not explicitly gated",
            "policy_sha256": "policy provenance digest missing",
            "whitelist_sha256": "whitelist provenance digest missing",
            "mutation_target_error": "shrink_index is not release-target guarded",
            "atomic_replace_text": "shrink_index bypasses atomic writes",
        },
        failures,
    )
    need("BUILTIN_OPEN_CITIES" not in shrink, "city policy lists leaked back into Python", failures)
    need("BUILTIN_OPEN_SERVICES" not in shrink, "service policy lists leaked back into Python", failures)
    need("/opt/p3-app/data/index_keep_config.json" not in shrink, "shrink_index uses legacy global keep-config", failures)

    require_tokens(
        health,
        {
            "normalize_policy_payload": "SEO healthcheck does not parse v2 policy",
            "policy_page_is_open": "SEO healthcheck does not validate exact pair indexability",
            "unexpected_noindex_open": "SEO healthcheck cannot detect wrong noindex",
            "unexpected_index_closed": "SEO healthcheck cannot detect wrongly indexed closed pages",
            "open_missing_sitemap": "SEO healthcheck cannot detect open URL missing from sitemap",
            "closed_in_sitemap": "SEO healthcheck cannot detect closed URL leaking into sitemap",
            "canonical_duplicate_pages": "SEO healthcheck cannot detect duplicate canonicals",
            "invalid_jsonld_pages": "SEO healthcheck cannot detect invalid JSON-LD",
            "broken_internal_links": "SEO healthcheck cannot detect broken internal links",
        },
        failures,
    )

    require_tokens(
        corpus,
        {
            "physical_pages": "full-corpus audit does not report physical inventory",
            "indexable_pages": "full-corpus audit does not report indexable inventory",
            "orphan_indexable_pages": "full-corpus audit does not detect orphan pages",
            "indexable_links_to_closed": "full-corpus audit does not detect open-to-closed links",
            "near_duplicate_pages": "full-corpus audit does not detect near duplicates",
        },
        failures,
    )

    require_tokens(
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

    require_tokens(
        quality,
        {
            "search evidence": "pair quality audit is not evidence-targeted",
            "thin_content": "pair quality audit does not detect thin pages",
            "canonical_mismatch": "pair quality audit does not validate self-canonical",
            "invalid_jsonld": "pair quality audit does not validate JSON-LD",
            "exact_duplicate": "pair quality audit does not detect exact duplicate bodies",
            "near_duplicate": "pair quality audit does not flag near duplicates",
            "improve_before_index": "pair quality audit has no hard quality state",
        },
        failures,
    )

    require_tokens(
        builder,
        {
            '"policy_version": 2': "pair-policy builder does not emit v2",
            '"example_only": True': "pair-policy candidate is not protected from direct production use",
            '"open_pairs"': "pair-policy builder does not emit exact pairs",
            "DEFAULT_QUALITY": "pair-policy builder does not consume pair quality",
            "require_quality": "pair-policy builder can no longer require quality evidence",
            "quality_hard_fail": "hard page-quality defects do not block pair recommendation",
            "review_similarity": "near duplicates cannot be surfaced for manual review",
        },
        failures,
    )

    require_tokens(
        predeploy,
        {
            "normalize_policy_payload": "predeploy does not structurally validate v1/v2 policy",
            "policy_digest": "predeploy does not verify the canonical policy digest",
            "release policy SHA-256 mismatch": "predeploy cannot detect changed open_pairs with stale digest",
            "verify_release_metadata": "predeploy does not verify finalized release fingerprint",
            "evaluate(audit)": "predeploy does not execute strict SEO thresholds",
        },
        failures,
    )

    require_tokens(
        report,
        {
            "_policy_pair_count": "SEO report does not expose exact policy pair count",
            "policy open city hubs": "SEO report does not expose city-hub inventory",
            "inside exact release policy": "SEO report does not compare search evidence to exact policy",
            "ZERO GSC page signal": "SEO report does not expose open pages without Google signals",
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
    require_tokens(
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


def check_release_control(failures: list[str]) -> None:
    safety = read("release_safety.py", failures)
    integrity = read("release_integrity.py", failures)
    predeploy = read("server-opt/predeploy_check.py", failures)
    deploy = read("server-opt/deploy_release.py", failures)
    bootstrap = read("server-opt/bootstrap_release_layout.py", failures)
    prune = read("server-opt/prune_releases.py", failures)
    finalizer = read("server-opt/finalize_release.py", failures)
    installer = read("server-opt/install_generator_facade.py", failures)

    require_tokens(
        safety,
        {
            "def mutation_target_error(": "release mutation guard missing",
            "def atomic_replace_text(": "atomic write helper missing",
            "def release_operation_lock(": "host-wide release lock missing",
            "fcntl.LOCK_EX | fcntl.LOCK_NB": "release lock is not exclusive/non-blocking",
            "O_NOFOLLOW": "release lock can follow a symlink",
        },
        failures,
    )
    require_tokens(
        integrity,
        {
            "def compute_release_digest(": "release digest implementation missing",
            "def verify_release_metadata(": "release metadata verification missing",
            "release content SHA-256 mismatch": "late release mutation is not detected",
        },
        failures,
    )
    need("verify_release_metadata" in predeploy, "predeploy does not verify immutable release fingerprint", failures)
    need("run_predeploy(" in deploy, "deploy does not run strict predeploy", failures)
    need("normalize_policy_payload" in deploy and "policy_digest" in deploy, "deploy structural gate does not validate v2 policy", failures)
    need("rollback target" in deploy, "deploy does not report rollback target", failures)
    need("run_predeploy(" in bootstrap, "bootstrap does not validate target release", failures)
    need("pre-bootstrap-" in prune, "prune does not protect bootstrap backup class", failures)
    need("write_release_metadata" in finalizer, "release finalizer does not write integrity metadata", failures)
    need('compile(source, str(path), "exec")' in installer, "generator installer does not syntax-check sources", failures)

    for label, text in (("deploy", deploy), ("bootstrap", bootstrap), ("prune", prune)):
        need("release_operation_lock" in text, f"{label}: host-wide release lock missing", failures)
        need("--lock-file" in text, f"{label}: lock path is not explicit/configurable", failures)


def check_validation_and_docs(failures: list[str]) -> None:
    ci = read(".github/workflows/ci.yml", failures)
    validator = read("scripts/validate_repo.py", failures)
    env = read(".env.example", failures)
    readme = read("README.md", failures)
    seo_arch = read("SEO_ARCHITECTURE.md", failures)
    seo_ops = read("SEO_OPERATIONS.md", failures)
    runbook = read("server-opt/PRODUCTION_DEPLOY_RUNBOOK.md", failures)

    for rel in TEST_FILES:
        read(rel, failures)

    require_tokens(
        validator,
        {
            '"-m", "compileall"': "validator lost compileall",
            'shutil.which("ruff")': "validator does not locate Ruff CLI",
            '"E9,F63,F7,F82"': "validator lost fatal Ruff rules",
            '"-m", "unittest"': "validator lost unit tests",
            "scripts/repo_healthcheck.py": "validator lost repository healthcheck",
            "scripts/seo_pipeline_healthcheck.py": "validator lost SEO pipeline healthcheck",
        },
        failures,
    )
    need("python scripts/validate_repo.py" in ci, "CI does not use shared validator", failures)
    need("SEOHC_REQUIRE_POLICY=true" in env, ".env.example does not document fail-closed SEO policy", failures)
    need("index_policy.py" in readme, "README does not document shared SEO policy model", failures)
    need("pair_quality_audit.py" in readme, "README does not document pair quality audit", failures)
    need("build_pair_policy.py" in readme, "README does not document pair-policy workflow", failures)
    need("SEO_ARCHITECTURE.md" in readme, "README does not link SEO architecture", failures)
    need("SEO_OPERATIONS.md" in readme, "README does not link SEO operations", failures)
    need("open_pairs" in seo_arch, "SEO architecture does not document exact pair model", failures)
    need("programmatic_seo_audit.py" in seo_arch, "SEO architecture does not document full-corpus audit", failures)
    need("build_search_evidence.py" in seo_arch, "SEO architecture does not document combined search evidence", failures)
    need("seo_snapshot.py" in seo_ops, "SEO operations does not document full snapshot command", failures)
    need("python scripts/validate_repo.py" in runbook, "production runbook skips repository validation", failures)


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
    check_programmatic_seo(failures)
    check_mutation_safety(failures)
    check_release_control(failures)
    check_validation_and_docs(failures)
    check_nginx_and_cleanup(failures)

    if failures:
        print("Repository healthcheck: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("Repository healthcheck: OK")
    print("  SEO policy v1 matrix + v2 exact city/service pairs share one model")
    print("  generator, sitemap, healthcheck and hub links use the same pair decision")
    print("  hardened generator strips fabricated proof/rating blocks before publication")
    print("  Yandex/GSC evidence is intersected with targeted pair quality before v2 recommendation")
    print("  full-corpus audit tracks thin/orphan/open-to-closed/duplicate risks")
    print("  pair-policy output is review-only and cannot auto-promote to production")
    print("  release writes/deploys remain candidate-only, fingerprinted and serialized")
    print("  CI/local validation share core + SEO pipeline healthchecks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
