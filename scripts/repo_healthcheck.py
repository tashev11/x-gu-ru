#!/usr/bin/env python3
"""Repository-level safety checks for x-gu.ru.

These checks intentionally avoid importing the private ``app.*`` backend, so
GitHub Actions and local validation can protect the same invariants.
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

WRITE_TO_PRODUCTION_SCRIPTS = (
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


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def check_templates(failures: list[str]) -> None:
    for name in TEMPLATE_NAMES:
        root_copy = ROOT / name
        prod_copy = ROOT / "server-opt" / "templates" / name
        require(root_copy.is_file(), f"missing root template: {name}", failures)
        require(prod_copy.is_file(), f"missing production template: {prod_copy}", failures)
        if root_copy.is_file() and prod_copy.is_file():
            require(root_copy.read_bytes() == prod_copy.read_bytes(), f"template copies diverged: {name}", failures)

    landing = (ROOT / "server-opt/templates/landing_master.html.j2").read_text(encoding="utf-8")
    hub = (ROOT / "server-opt/templates/city_hub_master.html.j2").read_text(encoding="utf-8")
    homepage = (ROOT / "server-opt/templates/homepage_master.html.j2").read_text(encoding="utf-8")

    for label, text in (("landing", landing), ("hub", hub), ("homepage", homepage)):
        require("cdn.tailwindcss.com" not in text, f"{label}: Tailwind Play CDN returned", failures)
        require("avitobibot" not in text, f"{label}: obsolete Telegram handle returned", failures)

    require("{{ robots_content" in hub, "city hub ignores robots_content", failures)
    require('/privacy/' in landing, "landing has no real privacy link", failures)
    require('/assets/tailwind.min.css' in landing, "landing does not use local Tailwind CSS", failures)


def check_generator(failures: list[str]) -> None:
    facade_path = ROOT / "content_generator.py"
    legacy_path = ROOT / "_content_generator_legacy.py"
    morphology_path = ROOT / "city_morphology.py"
    require(facade_path.is_file(), "content generator facade missing", failures)
    require(legacy_path.is_file(), "preserved legacy generator missing", failures)
    require(morphology_path.is_file(), "shared city morphology module missing", failures)
    if not facade_path.is_file():
        return

    facade = facade_path.read_text(encoding="utf-8")
    require("autoescape=True" in facade, "generator HTML autoescape is not enforced", failures)
    require("XGU_ALLOW_MISSING_KEEP_CONFIG" in facade, "missing index-policy fail-closed guard", failures)
    require("_sanitize_generated_html" in facade, "generated HTML sanitizer missing", failures)
    require('key in {"aggregateRating", "review"}' in facade, "rating/review schema sanitizer missing", failures)
    require('payload.get("@type") == "LocalBusiness"' in facade, "generated LocalBusiness sanitizer missing", failures)
    require("рост органики" in facade, "synthetic city KPI sanitizer missing", failures)
    require("reviewCount" not in facade, "synthetic review data leaked into public facade", failures)
    require("city_morphology import city_prepositional" in facade, "generator does not import shared city morphology", failures)
    require("_legacy._city_prepositional = city_prepositional" in facade, "legacy render path is not patched to shared city morphology", failures)

    canonical_pos = facade.find('here.parent / "server-opt" / "templates"')
    fallback_pos = facade.find('candidates.append(Path("app/templates"))')
    require(canonical_pos >= 0, "canonical repository template path missing", failures)
    require(fallback_pos >= 0, "legacy app/templates compatibility fallback missing", failures)
    require(
        canonical_pos >= 0 and fallback_pos >= 0 and canonical_pos < fallback_pos,
        "generator prefers legacy app/templates over canonical repository templates",
        failures,
    )


def check_seo_tooling(failures: list[str]) -> None:
    repair_path = ROOT / "seo_inplace_fix.py"
    health_path = ROOT / "seo_healthcheck.py"
    require(repair_path.is_file(), "seo_inplace_fix.py missing", failures)
    require(health_path.is_file(), "seo_healthcheck.py missing", failures)

    if repair_path.is_file():
        repair = repair_path.read_text(encoding="utf-8")
        require("from city_morphology import city_prepositional" in repair, "SEO repair duplicates city morphology instead of using shared module", failures)

    if health_path.is_file():
        health = health_path.read_text(encoding="utf-8")
        for token, message in (
            ("SEOHC_BASE_URL", "SEO healthcheck base URL is still hard-coded"),
            ("SEOHC_KEEP_CONFIG", "SEO healthcheck does not load index keep-config"),
            ("unexpected_noindex_open", "SEO healthcheck cannot detect open pages accidentally noindexed"),
            ("unexpected_index_closed", "SEO healthcheck cannot detect closed pages accidentally indexed"),
            ("open_missing_sitemap", "SEO healthcheck cannot detect open pages missing from sitemap"),
            ("closed_in_sitemap", "SEO healthcheck cannot detect closed pages leaking into sitemap"),
            ("canonical_url_mismatch", "SEO healthcheck does not validate canonical URL against page URL"),
            ("invalid_jsonld_pages", "SEO healthcheck cannot detect invalid JSON-LD"),
            ("broken_internal_links", "SEO healthcheck cannot detect broken internal links"),
            ("canonical_duplicate_pages", "SEO healthcheck cannot detect duplicate canonical URLs"),
        ):
            require(token in health, message, failures)
        require(
            "from app.services.notify_service import send_telegram" not in health.splitlines()[:20],
            "SEO healthcheck has a mandatory private-backend import at module load",
            failures,
        )


def check_index_policy(failures: list[str]) -> None:
    shrink_path = ROOT / "server-opt/shrink_index.py"
    example_path = ROOT / "server-opt/index_policy.example.json"
    baseline_path = ROOT / "server-opt/index_policy.baseline.json"
    require(shrink_path.is_file(), "shrink_index.py missing", failures)
    require(example_path.is_file(), "index policy example missing", failures)
    require(baseline_path.is_file(), "versioned index policy baseline missing", failures)

    if shrink_path.is_file():
        shrink = shrink_path.read_text(encoding="utf-8")
        require("BUNDLED_BASELINE" in shrink, "shrink_index does not use versioned baseline data", failures)
        require("--use-builtin-policy" in shrink, "emergency baseline is not explicitly gated", failures)
        require("example_only" in shrink, "shrink_index does not reject example-only policies", failures)
        require("policy_sha256" in shrink, "applied keep-config does not record policy hash", failures)
        require("BUILTIN_OPEN_CITIES" not in shrink, "city policy lists leaked back into Python", failures)
        require("BUILTIN_OPEN_SERVICES" not in shrink, "service policy lists leaked back into Python", failures)

    if example_path.is_file():
        payload = json.loads(example_path.read_text(encoding="utf-8"))
        require(payload.get("example_only") is True, "index policy example is not marked example_only", failures)

    if baseline_path.is_file():
        payload = json.loads(baseline_path.read_text(encoding="utf-8"))
        require(bool(payload.get("reviewed_at")), "baseline index policy has no review date", failures)
        require(bool(payload.get("source_note")), "baseline index policy has no source note", failures)
        require(bool(payload.get("open_cities")), "baseline index policy has no cities", failures)
        require(bool(payload.get("open_services")), "baseline index policy has no services", failures)
        require(not payload.get("example_only", False), "baseline index policy is marked example-only", failures)


def check_write_safety(failures: list[str]) -> None:
    for rel_path in WRITE_TO_PRODUCTION_SCRIPTS:
        path = ROOT / rel_path
        require(path.is_file(), f"maintenance script missing: {rel_path}", failures)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        require("--apply" in text, f"{rel_path}: production writes are not gated by --apply", failures)


def check_release_ops(failures: list[str]) -> None:
    deploy_path = ROOT / "server-opt/deploy_release.py"
    prune_path = ROOT / "server-opt/prune_releases.py"
    install_path = ROOT / "server-opt/install_generator_facade.py"
    disk_path = ROOT / "server-opt/disk-autoclean.sh"
    require(deploy_path.is_file(), "atomic release deploy helper missing", failures)
    require(prune_path.is_file(), "release retention helper missing", failures)
    require(install_path.is_file(), "generator facade installer missing", failures)
    require(disk_path.is_file(), "disk-autoclean.sh missing", failures)

    if deploy_path.is_file():
        deploy = deploy_path.read_text(encoding="utf-8")
        require("os.replace(temp_link, current)" in deploy, "release switch is no longer atomic", failures)
        require("is not a symlink" in deploy, "deploy no longer refuses a real current directory", failures)
        require("rollback target" in deploy, "deploy no longer reports rollback target", failures)

    if prune_path.is_file():
        prune = prune_path.read_text(encoding="utf-8")
        require("if path == active" in prune, "release pruning lacks active-release deletion guard", failures)
        require("resolved.parent != root" in prune, "release pruning can escape releases root", failures)
        require("current.is_symlink()" in prune, "release pruning cannot prove active release", failures)

    if install_path.is_file():
        installer = install_path.read_text(encoding="utf-8")
        require("REQUIRED_FILES" in installer and "city_morphology.py" in installer, "generator install set is incomplete", failures)
        require("Stage every new file before mutating any live target" in installer, "generator installer no longer stages full set first", failures)
        require("for target in reversed(replaced)" in installer, "generator installer lost rollback loop", failures)
        require("shutil.copy2(backup, target)" in installer, "generator installer no longer restores backups", failures)

    if disk_path.is_file():
        disk = disk_path.read_text(encoding="utf-8")
        require("JOURNAL_DAYS" in disk and "JOURNAL_MAX" in disk, "disk cleanup retention is not configurable", failures)
        require("--vacuum-time=3d" not in disk, "disk cleanup reverted to 3-day journal history", failures)
        require("--vacuum-size=15M" not in disk, "disk cleanup reverted to 15M journal cap", failures)
        require("auth.log" not in disk, "disk cleanup directly targets authentication logs", failures)


def check_ci_and_tests(failures: list[str]) -> None:
    ci_path = ROOT / ".github/workflows/ci.yml"
    validator_path = ROOT / "scripts/validate_repo.py"
    require(ci_path.is_file(), "CI workflow missing", failures)
    require(validator_path.is_file(), "shared local/CI validator missing", failures)

    for test_file, message in (
        ("tests/test_city_morphology.py", "city morphology tests missing"),
        ("tests/test_seo_healthcheck.py", "SEO healthcheck tests missing"),
        ("tests/test_deploy_release.py", "atomic deploy tests missing"),
        ("tests/test_prune_releases.py", "release retention tests missing"),
        ("tests/test_install_generator_facade.py", "generator installer rollback tests missing"),
        ("tests/test_shrink_index_policy.py", "index policy safety tests missing"),
    ):
        require((ROOT / test_file).is_file(), message, failures)

    if validator_path.is_file():
        validator = validator_path.read_text(encoding="utf-8")
        for token, message in (
            ("-m\", \"compileall", "shared validator lost Python compile check"),
            ("-m\", \"ruff", "shared validator lost Ruff fatal-error check"),
            ("-m\", \"unittest", "shared validator lost unit tests"),
            ("scripts/repo_healthcheck.py", "shared validator lost repository invariant checks"),
        ):
            require(token in validator, message, failures)

    if ci_path.is_file():
        ci = ci_path.read_text(encoding="utf-8")
        require("python scripts/validate_repo.py" in ci, "GitHub CI does not use shared validator", failures)
        require("python -m pip install --disable-pip-version-check ruff" in ci, "CI no longer installs Ruff for validator", failures)


def check_nginx(failures: list[str]) -> None:
    nginx_dir = ROOT / "server-opt/nginx"
    require(not (nginx_dir / "x-gu.ru.conf.current").exists(), "stale x-gu.ru.conf.current exists", failures)
    require(not (nginx_dir / "x-gu.ru.conf.new").exists(), "stale x-gu.ru.conf.new exists", failures)

    nginx = (nginx_dir / "nginx.conf").read_text(encoding="utf-8")
    vhost = (nginx_dir / "x-gu.ru.conf").read_text(encoding="utf-8")
    require("server_tokens off;" in nginx, "nginx exposes server version", failures)
    require("zone=lead_submit" in nginx, "lead rate-limit zone missing", failures)
    require("location = /api/v1/leads/submit" in vhost, "lead endpoint rate limit missing", failures)
    require("return 301 https://x-gu.ru$request_uri;" in vhost, "canonical www/http redirect missing", failures)
    require('X-Content-Type-Options "nosniff"' in vhost, "security headers missing", failures)


def check_repository_shape(failures: list[str]) -> None:
    require((ROOT / "README.md").is_file(), "README.md missing", failures)
    require((ROOT / ".env.example").is_file(), ".env.example missing", failures)
    require((ROOT / "requirements.txt").is_file(), "requirements.txt missing", failures)


def main() -> int:
    failures: list[str] = []
    check_templates(failures)
    check_generator(failures)
    check_seo_tooling(failures)
    check_index_policy(failures)
    check_write_safety(failures)
    check_release_ops(failures)
    check_ci_and_tests(failures)
    check_nginx(failures)
    check_repository_shape(failures)

    if failures:
        print("Repository healthcheck: FAIL")
        for item in failures:
            print(f"  - {item}")
        return 1

    print("Repository healthcheck: OK")
    print("  templates synchronized")
    print("  generator fail-closed/sanitization guards present")
    print("  generator and repair tools share city morphology")
    print("  SEO healthcheck covers index policy, sitemap, canonical, JSON-LD and internal links")
    print("  reviewed index policy is external/versioned and auditable")
    print("  atomic release deploy + safe release retention are guarded")
    print("  generator facade installation stages + rolls back as a set")
    print("  GitHub CI and local checks share one validation entrypoint")
    print("  canonical repository templates take priority")
    print("  production maintenance scripts require --apply")
    print("  canonical robots/template fixes present")
    print("  nginx canonicalization and hardening present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
