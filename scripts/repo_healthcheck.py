#!/usr/bin/env python3
"""Repository-level safety checks for x-gu.ru.

These checks avoid importing the private ``app.*`` backend. They intentionally
verify architecture/safety properties that are expensive to discover only after
a programmatic SEO release has reached production.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

TEMPLATE_NAMES = (
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
)

RELEASE_FIRST_MUTATORS = (
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
)

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
)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read_text(rel_path: str, failures: list[str]) -> str:
    path = ROOT / rel_path
    if not path.is_file():
        failures.append(f"missing file: {rel_path}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        failures.append(f"cannot read {rel_path}: {exc}")
        return ""


def require_tokens(text: str, checks: tuple[tuple[str, str], ...], failures: list[str]) -> None:
    for token, message in checks:
        require(token in text, message, failures)


def check_templates(failures: list[str]) -> None:
    for name in TEMPLATE_NAMES:
        root_copy = ROOT / name
        production_copy = ROOT / "server-opt" / "templates" / name
        require(root_copy.is_file(), f"missing root template: {name}", failures)
        require(production_copy.is_file(), f"missing production template: {name}", failures)
        if root_copy.is_file() and production_copy.is_file():
            require(root_copy.read_bytes() == production_copy.read_bytes(), f"template copies diverged: {name}", failures)

    landing = read_text("server-opt/templates/landing_master.html.j2", failures)
    hub = read_text("server-opt/templates/city_hub_master.html.j2", failures)
    homepage = read_text("server-opt/templates/homepage_master.html.j2", failures)
    for label, text in (("landing", landing), ("hub", hub), ("homepage", homepage)):
        require("cdn.tailwindcss.com" not in text, f"{label}: Tailwind Play CDN returned", failures)
        require("avitobibot" not in text, f"{label}: obsolete Telegram handle returned", failures)
    require("{{ robots_content" in hub, "city hub ignores robots_content", failures)
    require("/privacy/" in landing, "landing has no real privacy link", failures)
    require("/assets/tailwind.min.css" in landing, "landing does not use local Tailwind CSS", failures)


def check_generator(failures: list[str]) -> None:
    facade = read_text("content_generator.py", failures)
    read_text("_content_generator_legacy.py", failures)
    read_text("city_morphology.py", failures)
    require_tokens(
        facade,
        (
            ("autoescape=True", "generator HTML autoescape is not enforced"),
            ('_RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"', "release keep manifest constant missing"),
            ('_RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"', "release whitelist snapshot constant missing"),
            ("XGU_KEEP_CONFIG", "candidate keep-config override missing"),
            ("XGU_WHITELIST", "candidate whitelist override missing"),
            ("XGU_CURRENT_ROOT", "active release root is not configurable"),
            ("XGU_ALLOW_LEGACY_KEEP_CONFIG", "legacy keep-config is not explicitly gated"),
            ("XGU_ALLOW_LEGACY_WHITELIST", "legacy whitelist is not explicitly gated"),
            ("XGU_ALLOW_MISSING_KEEP_CONFIG", "missing-policy migration override missing"),
            ("hashlib.sha256(whitelist.read_bytes()).hexdigest()", "generator does not verify release whitelist hash"),
            ("_sanitize_generated_html", "generated HTML sanitizer missing"),
            ('key in {"aggregateRating", "review"}', "rating/review schema sanitizer missing"),
            ('payload.get("@type") == "LocalBusiness"', "generated LocalBusiness sanitizer missing"),
            ("from .city_morphology import city_prepositional", "private-backend morphology import missing"),
            ("from city_morphology import city_prepositional", "root morphology fallback missing"),
            ("_legacy._city_prepositional = city_prepositional", "legacy renderer does not use shared morphology"),
        ),
        failures,
    )
    require("reviewCount" not in facade, "synthetic review data leaked into public facade", failures)

    canonical_pos = facade.find('candidates.append(root / "server-opt" / "templates")')
    fallback_pos = facade.find('candidates.append(Path("app/templates"))')
    require(canonical_pos >= 0, "canonical repository template path missing", failures)
    require(fallback_pos >= 0, "legacy app/templates fallback missing", failures)
    require(
        canonical_pos >= 0 and fallback_pos >= 0 and canonical_pos < fallback_pos,
        "generator prefers legacy app/templates over canonical templates",
        failures,
    )

    keep_gate = facade.find('if _env_true("XGU_ALLOW_LEGACY_KEEP_CONFIG")')
    keep_lookup = facade.find('_data_file("index_keep_config.json")')
    whitelist_gate = facade.find('if _env_true("XGU_ALLOW_LEGACY_WHITELIST")')
    whitelist_lookup = facade.find('_data_file("whitelist.txt")')
    require(keep_gate >= 0 and keep_lookup > keep_gate, "legacy keep-config fallback is not gated", failures)
    require(
        whitelist_gate >= 0 and whitelist_lookup > whitelist_gate,
        "legacy whitelist fallback is not gated",
        failures,
    )


def check_seo_tooling(failures: list[str]) -> None:
    repair = read_text("seo_inplace_fix.py", failures)
    health = read_text("seo_healthcheck.py", failures)
    predeploy = read_text("server-opt/predeploy_check.py", failures)
    require("from city_morphology import city_prepositional" in repair, "SEO repair duplicates city morphology", failures)

    require_tokens(
        health,
        (
            ("RELEASE_KEEP_FILENAME", "SEO healthcheck is not release-manifest aware"),
            ("RELEASE_WHITELIST_FILENAME", "SEO healthcheck is not release-whitelist aware"),
            ("SEOHC_REQUIRE_POLICY", "SEO healthcheck does not fail closed on missing policy"),
            ("SEOHC_ALLOW_LEGACY_KEEP_CONFIG", "SEO legacy keep fallback is not opt-in"),
            ("SEOHC_ALLOW_LEGACY_WHITELIST", "SEO legacy whitelist fallback is not opt-in"),
            ("release whitelist SHA-256 mismatch", "SEO healthcheck does not verify whitelist integrity"),
            ("unexpected_noindex_open", "SEO healthcheck cannot detect open pages accidentally noindexed"),
            ("unexpected_index_closed", "SEO healthcheck cannot detect closed pages accidentally indexed"),
            ("open_missing_sitemap", "SEO healthcheck cannot detect open pages missing from sitemap"),
            ("closed_in_sitemap", "SEO healthcheck cannot detect closed pages leaking into sitemap"),
            ("bad_sitemap_urls", "SEO healthcheck cannot detect malformed/noncanonical sitemap URLs"),
            ("sitemap_orphan_urls", "SEO healthcheck cannot detect sitemap URLs without pages"),
            ("canonical_url_mismatch", "SEO healthcheck does not validate canonical URL"),
            ("canonical_duplicate_pages", "SEO healthcheck cannot detect duplicate canonical URLs"),
            ("invalid_jsonld_pages", "SEO healthcheck cannot detect invalid JSON-LD"),
            ("broken_internal_links", "SEO healthcheck cannot detect broken internal links"),
        ),
        failures,
    )
    require(
        "from app.services.notify_service import send_telegram" not in health.splitlines()[:20],
        "SEO healthcheck requires private backend at import time",
        failures,
    )

    require_tokens(
        predeploy,
        (
            ('KEEP_FILENAME = ".xgu-index-keep.json"', "predeploy is not bound to release manifest"),
            ('WHITELIST_FILENAME = ".xgu-whitelist.txt"', "predeploy is not bound to release whitelist"),
            ("whitelist must be the release snapshot", "predeploy accepts an unrelated whitelist"),
            ("whitelist_sha256", "predeploy does not require whitelist hash"),
            ("release whitelist SHA-256 mismatch", "predeploy does not verify whitelist hash"),
            ('audit.get("policy_loaded")', "predeploy does not verify policy was loaded"),
            ("policy-check every HTML page", "predeploy does not require complete policy coverage"),
            ("evaluate(audit)", "predeploy does not run strict SEO thresholds"),
        ),
        failures,
    )


def check_index_policy(failures: list[str]) -> None:
    shrink = read_text("server-opt/shrink_index.py", failures)
    example_text = read_text("server-opt/index_policy.example.json", failures)
    baseline_text = read_text("server-opt/index_policy.baseline.json", failures)
    require_tokens(
        shrink,
        (
            ("BUNDLED_BASELINE", "shrink_index does not use versioned baseline data"),
            ("--use-builtin-policy", "emergency baseline is not explicitly gated"),
            ("example_only", "shrink_index does not reject example-only policies"),
            ('RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"', "shrink_index does not write release policy manifest"),
            ('RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"', "shrink_index does not snapshot whitelist into release"),
            ("write_release_keep_config", "shrink_index does not create release manifest"),
            ("write_release_whitelist", "shrink_index does not create release whitelist snapshot"),
            ("whitelist_sha256", "release manifest does not record whitelist hash"),
            ("whitelist_source", "release manifest does not record whitelist provenance"),
            ("mutation_target_error", "shrink_index is not release-target guarded"),
            ("atomic_replace_text", "shrink_index does not use atomic replacement"),
        ),
        failures,
    )
    require("BUILTIN_OPEN_CITIES" not in shrink, "city policy lists leaked back into Python", failures)
    require("BUILTIN_OPEN_SERVICES" not in shrink, "service policy lists leaked back into Python", failures)
    require(
        "/opt/p3-app/data/index_keep_config.json" not in shrink,
        "shrink_index still writes/depends on global keep-config",
        failures,
    )

    try:
        example = json.loads(example_text) if example_text else {}
        baseline = json.loads(baseline_text) if baseline_text else {}
    except json.JSONDecodeError as exc:
        failures.append(f"index policy JSON is invalid: {exc}")
        return
    require(example.get("example_only") is True, "index policy example is not marked example_only", failures)
    require(bool(baseline.get("reviewed_at")), "baseline index policy has no review date", failures)
    require(bool(baseline.get("source_note")), "baseline index policy has no source note", failures)
    require(bool(baseline.get("open_cities")), "baseline index policy has no cities", failures)
    require(bool(baseline.get("open_services")), "baseline index policy has no services", failures)
    require(not baseline.get("example_only", False), "baseline index policy is marked example-only", failures)


def check_write_safety(failures: list[str]) -> None:
    for rel_path in PRODUCTION_MUTATORS:
        text = read_text(rel_path, failures)
        if text:
            require("--apply" in text, f"{rel_path}: production writes are not gated by --apply", failures)

    for rel_path in RELEASE_FIRST_MUTATORS:
        text = read_text(rel_path, failures)
        if not text:
            continue
        require("mutation_target_error" in text, f"{rel_path}: release target guard missing", failures)
        require(
            "--unsafe-allow-active-current" in text,
            f"{rel_path}: active-current writes are not an explicit emergency override",
            failures,
        )
        require(
            ".write_text(" not in text,
            f"{rel_path}: direct text write bypasses shared atomic helper",
            failures,
        )

    for rel_path in BACKEND_GENERATOR_CONSUMERS:
        text = read_text(rel_path, failures)
        if not text:
            continue
        require(
            "from app.services.content_generator import" in text,
            f"{rel_path}: does not import the generator from its installed app.services location",
            failures,
        )
        require(
            "from content_generator import" not in text,
            f"{rel_path}: can accidentally import a stale top-level content_generator",
            failures,
        )

    rebuild = read_text("seo_rebuild_broken.py", failures)
    open_hubs = read_text("server-opt/rerender_open_hubs.py", failures)
    all_hubs = read_text("server-opt/rerender_hubs_home.py", failures)
    for label, text in (("targeted rebuild", rebuild), ("open-hub rerender", open_hubs), ("hub/home rerender", all_hubs)):
        require(".xgu-index-keep.json" in text, f"{label}: release manifest is not required", failures)
        require(".xgu-whitelist.txt" in text, f"{label}: release whitelist is not required", failures)
        require("XGU_KEEP_CONFIG" in text, f"{label}: renderer is not bound to candidate policy", failures)
        require("XGU_WHITELIST" in text, f"{label}: renderer is not bound to candidate whitelist", failures)
        require("whitelist SHA-256 mismatch" in text, f"{label}: whitelist integrity is not verified", failures)

    require(
        "cities_for_home" in all_hubs and "open_cities" in all_hubs,
        "full hub rerender can repopulate homepage with closed cities",
        failures,
    )
    require(
        "allowed_slugs" in all_hubs and "open_services" in all_hubs,
        "full hub rerender does not constrain open-hub services by policy",
        failures,
    )


def check_release_ops(failures: list[str]) -> None:
    safety = read_text("release_safety.py", failures)
    deploy = read_text("server-opt/deploy_release.py", failures)
    prune = read_text("server-opt/prune_releases.py", failures)
    installer = read_text("server-opt/install_generator_facade.py", failures)
    disk = read_text("server-opt/disk-autoclean.sh", failures)

    require_tokens(
        safety,
        (
            ("def mutation_target_error(", "shared release mutation guard missing"),
            ("resolved.parent != releases", "release guard accepts non-direct candidates"),
            ("resolved == active", "release guard does not protect active current"),
            ("def atomic_replace_text(", "shared atomic text helper missing"),
            ("os.replace(temp, path)", "shared atomic helper no longer uses os.replace"),
            ("parent directory does not exist", "atomic helper silently creates missing parent paths"),
        ),
        failures,
    )

    require_tokens(
        deploy,
        (
            ("os.replace(temp_link, current)", "release switch is no longer atomic"),
            ("release_resolved.parent != root_resolved", "deploy can target arbitrary directory"),
            ('KEEP_FILENAME = ".xgu-index-keep.json"', "deploy does not require release policy"),
            ('WHITELIST_FILENAME = ".xgu-whitelist.txt"', "deploy does not require release whitelist"),
            ("whitelist_sha256", "deploy does not validate whitelist metadata"),
            ("referenced sitemap shard missing", "deploy does not validate sitemap shards"),
            ("run_predeploy(", "deploy does not execute strict predeploy gate"),
            ("whitelist=release / WHITELIST_FILENAME", "deploy predeploy does not use embedded whitelist"),
            ("--unsafe-skip-predeploy", "predeploy bypass is not explicitly emergency-only"),
            ("is not a symlink", "deploy no longer refuses a real current directory"),
            ("rollback target", "deploy no longer reports rollback target"),
        ),
        failures,
    )

    require_tokens(
        prune,
        (
            ("target.parent != root", "release pruning accepts non-direct current targets"),
            ("_delete_if_still_inactive", "release pruning does not re-check current before delete"),
            ("active release cannot be proven immediately before delete", "release pruning lacks race fail-closed guard"),
            ("resolved.parent != root", "release pruning can escape releases root"),
            ("current.is_symlink()", "release pruning cannot prove active release"),
        ),
        failures,
    )

    require_tokens(
        installer,
        (
            ("REQUIRED_FILES", "generator install set is missing"),
            ("city_morphology.py", "generator install set is incomplete"),
            ('compile(source, str(path), "exec")', "generator installer does not syntax-check sources"),
            ("Stage every new file before mutating any live target", "generator installer no longer stages full set first"),
            ("for target in reversed(replaced)", "generator installer lost rollback loop"),
            (".rollback.", "generator installer rollback is not staged"),
            ("os.replace(restore_temp, target)", "generator installer rollback is not atomic"),
        ),
        failures,
    )

    require("JOURNAL_DAYS" in disk and "JOURNAL_MAX" in disk, "disk cleanup retention is not configurable", failures)
    require("--vacuum-time=3d" not in disk, "disk cleanup reverted to 3-day journal history", failures)
    require("--vacuum-size=15M" not in disk, "disk cleanup reverted to 15M journal cap", failures)
    require("auth.log" not in disk, "disk cleanup directly targets authentication logs", failures)


def check_purge_safety(failures: list[str]) -> None:
    purge = read_text("server-opt/purge_closed_pages.py", failures)
    require_tokens(
        purge,
        (
            ('WHITELIST_FILENAME = ".xgu-whitelist.txt"', "purge does not use release whitelist snapshot"),
            ("validate_release_contract", "purge does not validate release contract"),
            ("release whitelist SHA-256 mismatch", "purge does not verify whitelist integrity"),
            ("if not index_file.is_file():", "purge can delete a directory without index.html"),
            ("if not _explicit_noindex(index_file):", "purge can delete a page without confirmed noindex"),
            ("mutation_target_error", "purge is not release-target guarded"),
            ("for path, url in to_delete:", "purge has no per-delete loop for race re-check"),
            ("_safe_delete(path, args.root)", "purge bypasses safe delete path check"),
        ),
        failures,
    )
    require(
        purge.count("mutation_target_error(") >= 2,
        "purge does not re-check release activity before each destructive delete",
        failures,
    )


def check_ci_and_tests(failures: list[str]) -> None:
    ci = read_text(".github/workflows/ci.yml", failures)
    validator = read_text("scripts/validate_repo.py", failures)
    for test_file in TEST_FILES:
        read_text(test_file, failures)

    require_tokens(
        validator,
        (
            ('"-m", "compileall"', "shared validator lost Python compile check"),
            ('shutil.which("ruff")', "shared validator does not locate Ruff CLI"),
            ('"E9,F63,F7,F82"', "shared validator lost Ruff fatal-error rules"),
            ('"-m", "unittest"', "shared validator lost unit tests"),
            ("scripts/repo_healthcheck.py", "shared validator lost repository invariants"),
        ),
        failures,
    )
    require('importlib.util.find_spec("ruff")' not in validator, "validator incorrectly assumes Ruff is importable", failures)
    require("python scripts/validate_repo.py" in ci, "GitHub CI does not use shared validator", failures)
    require("python -m pip install --disable-pip-version-check ruff" in ci, "CI no longer installs Ruff", failures)


def check_nginx(failures: list[str]) -> None:
    nginx_dir = ROOT / "server-opt/nginx"
    require(not (nginx_dir / "x-gu.ru.conf.current").exists(), "stale x-gu.ru.conf.current exists", failures)
    require(not (nginx_dir / "x-gu.ru.conf.new").exists(), "stale x-gu.ru.conf.new exists", failures)
    nginx = read_text("server-opt/nginx/nginx.conf", failures)
    vhost = read_text("server-opt/nginx/x-gu.ru.conf", failures)
    require("server_tokens off;" in nginx, "nginx exposes server version", failures)
    require("zone=lead_submit" in nginx, "lead rate-limit zone missing", failures)
    require("location = /api/v1/leads/submit" in vhost, "lead endpoint rate limit missing", failures)
    require("limit_req_status 429;" in vhost, "lead rate limiting does not return 429", failures)
    require("return 301 https://x-gu.ru$request_uri;" in vhost, "canonical www/http redirect missing", failures)
    require('X-Content-Type-Options "nosniff"' in vhost, "security headers missing", failures)


def check_repository_shape(failures: list[str]) -> None:
    for rel_path in (
        "README.md",
        ".env.example",
        "requirements.txt",
        ".github/workflows/ci.yml",
        "release_safety.py",
        "server-opt/predeploy_check.py",
    ):
        read_text(rel_path, failures)

    env_example = read_text(".env.example", failures)
    require("XGU_ALLOW_LEGACY_WHITELIST" in env_example, ".env.example omits legacy whitelist migration gate", failures)
    require("SEOHC_ALLOW_LEGACY_WHITELIST" in env_example, ".env.example omits SEO legacy whitelist migration gate", failures)
    require("SEOHC_REQUIRE_POLICY=true" in env_example, ".env.example does not document fail-closed SEO policy", failures)


def main() -> int:
    failures: list[str] = []
    check_templates(failures)
    check_generator(failures)
    check_seo_tooling(failures)
    check_index_policy(failures)
    check_write_safety(failures)
    check_release_ops(failures)
    check_purge_safety(failures)
    check_ci_and_tests(failures)
    check_nginx(failures)
    check_repository_shape(failures)

    if failures:
        print("Repository healthcheck: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("Repository healthcheck: OK")
    print("  templates synchronized and canonical")
    print("  generator policy + whitelist follow the active release")
    print("  legacy global policy/whitelist require explicit migration flags")
    print("  backend maintenance imports the installed app.services generator")
    print("  shared city morphology is enforced")
    print("  SEO healthcheck is fail-closed and release-contract aware")
    print("  strict predeploy verifies embedded policy + whitelist integrity")
    print("  release-first mutators reject active current and avoid direct text writes")
    print("  homepage/open-hub rerenders are candidate-policy constrained")
    print("  purge requires index.html + noindex and re-checks active release")
    print("  deploy validates self-contained release before atomic switch")
    print("  release pruning re-checks current immediately before deletion")
    print("  generator install is syntax-checked, staged and rollback-safe")
    print("  GitHub CI and local checks share one validation entrypoint")
    print("  nginx canonicalization, headers and lead rate limiting are guarded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
