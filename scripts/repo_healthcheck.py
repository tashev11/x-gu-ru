#!/usr/bin/env python3
"""Repository-level safety checks for x-gu.ru.

The checker intentionally validates repository text/shape without importing the
private ``app.*`` backend. GitHub Actions and local validation therefore guard
the same production invariants.
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

TEST_FILES = (
    "tests/test_city_morphology.py",
    "tests/test_seo_healthcheck.py",
    "tests/test_deploy_release.py",
    "tests/test_prune_releases.py",
    "tests/test_install_generator_facade.py",
    "tests/test_shrink_index_policy.py",
)


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def read_text(rel_path: str, failures: list[str]) -> str:
    path = ROOT / rel_path
    if not path.is_file():
        failures.append(f"missing file: {rel_path}")
        return ""
    return path.read_text(encoding="utf-8")


def require_tokens(text: str, checks: tuple[tuple[str, str], ...], failures: list[str]) -> None:
    for token, message in checks:
        require(token in text, message, failures)


def read_json(rel_path: str, failures: list[str]) -> dict:
    text = read_text(rel_path, failures)
    if not text:
        return {}
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        failures.append(f"invalid JSON in {rel_path}: {exc}")
        return {}
    if not isinstance(value, dict):
        failures.append(f"JSON root must be an object: {rel_path}")
        return {}
    return value


def check_templates(failures: list[str]) -> None:
    for name in TEMPLATE_NAMES:
        root_copy = ROOT / name
        canonical = ROOT / "server-opt" / "templates" / name
        require(root_copy.is_file(), f"missing root template: {name}", failures)
        require(canonical.is_file(), f"missing canonical template: server-opt/templates/{name}", failures)
        if root_copy.is_file() and canonical.is_file():
            require(root_copy.read_bytes() == canonical.read_bytes(), f"template copies diverged: {name}", failures)

    landing = read_text("server-opt/templates/landing_master.html.j2", failures)
    hub = read_text("server-opt/templates/city_hub_master.html.j2", failures)
    homepage = read_text("server-opt/templates/homepage_master.html.j2", failures)

    for label, text in (("landing", landing), ("hub", hub), ("homepage", homepage)):
        require("cdn.tailwindcss.com" not in text, f"{label}: Tailwind Play CDN returned", failures)
        require("avitobibot" not in text, f"{label}: obsolete Telegram handle returned", failures)

    require("{{ robots_content" in hub, "city hub ignores robots_content", failures)
    require('/privacy/' in landing, "landing has no real privacy link", failures)
    require('/assets/tailwind.min.css' in landing, "landing does not use local Tailwind CSS", failures)


def check_generator(failures: list[str]) -> None:
    facade = read_text("content_generator.py", failures)
    read_text("_content_generator_legacy.py", failures)
    read_text("city_morphology.py", failures)
    if not facade:
        return

    require_tokens(
        facade,
        (
            ("autoescape=True", "generator HTML autoescape is not enforced"),
            ("XGU_ALLOW_MISSING_KEEP_CONFIG", "missing index-policy fail-closed guard"),
            ("_sanitize_generated_html", "generated HTML sanitizer missing"),
            ('key in {"aggregateRating", "review"}', "rating/review schema sanitizer missing"),
            ('payload.get("@type") == "LocalBusiness"', "generated LocalBusiness sanitizer missing"),
            ("рост органики", "synthetic city KPI sanitizer missing"),
            ("from .city_morphology import city_prepositional", "private-backend relative morphology import missing"),
            ("from city_morphology import city_prepositional", "root-execution morphology fallback missing"),
            ("_legacy._city_prepositional = city_prepositional", "legacy render path is not patched to shared morphology"),
        ),
        failures,
    )
    require("reviewCount" not in facade, "synthetic review data leaked into public facade", failures)

    canonical_pos = facade.find('here.parent / "server-opt" / "templates"')
    fallback_pos = facade.find('candidates.append(Path("app/templates"))')
    require(canonical_pos >= 0, "canonical repository template path missing", failures)
    require(fallback_pos >= 0, "legacy app/templates fallback missing", failures)
    require(
        canonical_pos >= 0 and fallback_pos >= 0 and canonical_pos < fallback_pos,
        "generator prefers legacy app/templates over canonical repository templates",
        failures,
    )


def check_seo_tooling(failures: list[str]) -> None:
    repair = read_text("seo_inplace_fix.py", failures)
    health = read_text("seo_healthcheck.py", failures)
    require(
        "from city_morphology import city_prepositional" in repair,
        "SEO repair duplicates city morphology instead of using shared module",
        failures,
    )
    require_tokens(
        health,
        (
            ("SEOHC_BASE_URL", "SEO healthcheck base URL is still hard-coded"),
            ("SEOHC_KEEP_CONFIG", "SEO healthcheck does not load index keep-config"),
            ("unexpected_noindex_open", "SEO healthcheck cannot detect open pages accidentally noindexed"),
            ("unexpected_index_closed", "SEO healthcheck cannot detect closed pages accidentally indexed"),
            ("open_missing_sitemap", "SEO healthcheck cannot detect open pages missing from sitemap"),
            ("closed_in_sitemap", "SEO healthcheck cannot detect closed pages leaking into sitemap"),
            ("canonical_url_mismatch", "SEO healthcheck does not compare canonical with page URL"),
            ("canonical_duplicate_pages", "SEO healthcheck cannot detect duplicate canonical URLs"),
            ("invalid_jsonld_pages", "SEO healthcheck cannot detect invalid JSON-LD"),
            ("broken_internal_links", "SEO healthcheck cannot detect broken internal links"),
        ),
        failures,
    )
    require(
        "from app.services.notify_service import send_telegram" not in health.splitlines()[:20],
        "SEO healthcheck has a mandatory private-backend import at module load",
        failures,
    )


def check_index_policy(failures: list[str]) -> None:
    shrink = read_text("server-opt/shrink_index.py", failures)
    example = read_json("server-opt/index_policy.example.json", failures)
    baseline = read_json("server-opt/index_policy.baseline.json", failures)

    require_tokens(
        shrink,
        (
            ("BUNDLED_BASELINE", "shrink_index does not use versioned baseline data"),
            ("--use-builtin-policy", "emergency baseline is not explicitly gated"),
            ("example_only", "shrink_index does not reject example-only policies"),
            ("policy_sha256", "applied keep-config does not record policy hash"),
        ),
        failures,
    )
    require("BUILTIN_OPEN_CITIES" not in shrink, "city policy lists leaked back into Python", failures)
    require("BUILTIN_OPEN_SERVICES" not in shrink, "service policy lists leaked back into Python", failures)

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


def check_release_ops(failures: list[str]) -> None:
    deploy = read_text("server-opt/deploy_release.py", failures)
    prune = read_text("server-opt/prune_releases.py", failures)
    installer = read_text("server-opt/install_generator_facade.py", failures)
    disk = read_text("server-opt/disk-autoclean.sh", failures)

    require_tokens(
        deploy,
        (
            ("os.replace(temp_link, current)", "release switch is no longer atomic"),
            ("is not a symlink", "deploy no longer refuses a real current directory"),
            ("rollback target", "deploy no longer reports rollback target"),
        ),
        failures,
    )
    require_tokens(
        prune,
        (
            ("if path == active", "release pruning lacks active-release deletion guard"),
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
            ("Stage every new file before mutating any live target", "generator installer no longer stages full set first"),
            ("for target in reversed(replaced)", "generator installer lost rollback loop"),
            ("shutil.copy2(backup, target)", "generator installer no longer restores backups"),
        ),
        failures,
    )
    require("JOURNAL_DAYS" in disk and "JOURNAL_MAX" in disk, "disk cleanup retention is not configurable", failures)
    require("--vacuum-time=3d" not in disk, "disk cleanup reverted to 3-day journal history", failures)
    require("--vacuum-size=15M" not in disk, "disk cleanup reverted to 15M journal cap", failures)
    require("auth.log" not in disk, "disk cleanup directly targets authentication logs", failures)


def check_ci_and_tests(failures: list[str]) -> None:
    ci = read_text(".github/workflows/ci.yml", failures)
    validator = read_text("scripts/validate_repo.py", failures)
    for test_file in TEST_FILES:
        read_text(test_file, failures)

    require_tokens(
        validator,
        (
            ('"-m", "compileall"', "shared validator lost Python compile check"),
            ('shutil.which("ruff")', "shared validator does not locate the supported Ruff CLI executable"),
            ('"E9,F63,F7,F82"', "shared validator lost Ruff fatal-error rules"),
            ('"-m", "unittest"', "shared validator lost unit tests"),
            ("scripts/repo_healthcheck.py", "shared validator lost repository invariant checks"),
        ),
        failures,
    )
    require(
        'importlib.util.find_spec("ruff")' not in validator,
        "shared validator incorrectly assumes Ruff is an importable Python module",
        failures,
    )
    require("python scripts/validate_repo.py" in ci, "GitHub CI does not use shared validator", failures)
    require(
        "python -m pip install --disable-pip-version-check ruff" in ci,
        "CI no longer installs Ruff for validator",
        failures,
    )


def check_nginx(failures: list[str]) -> None:
    nginx_dir = ROOT / "server-opt/nginx"
    require(not (nginx_dir / "x-gu.ru.conf.current").exists(), "stale x-gu.ru.conf.current exists", failures)
    require(not (nginx_dir / "x-gu.ru.conf.new").exists(), "stale x-gu.ru.conf.new exists", failures)

    nginx = read_text("server-opt/nginx/nginx.conf", failures)
    vhost = read_text("server-opt/nginx/x-gu.ru.conf", failures)
    require_tokens(
        nginx,
        (
            ("server_tokens off;", "nginx exposes server version"),
            ("zone=lead_submit", "lead rate-limit zone missing"),
        ),
        failures,
    )
    require_tokens(
        vhost,
        (
            ("location = /api/v1/leads/submit", "lead endpoint rate limit missing"),
            ("return 301 https://x-gu.ru$request_uri;", "canonical www/http redirect missing"),
            ('X-Content-Type-Options "nosniff"', "security headers missing"),
        ),
        failures,
    )


def check_repository_shape(failures: list[str]) -> None:
    for rel_path in ("README.md", ".env.example", "requirements.txt"):
        read_text(rel_path, failures)


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
    print("  generator supports package and root morphology imports")
    print("  SEO healthcheck covers index policy, sitemap, canonical, JSON-LD and internal links")
    print("  reviewed index policy is external/versioned and auditable")
    print("  atomic release deploy + safe release retention are guarded")
    print("  generator facade installation stages + rolls back as a set")
    print("  GitHub CI and local checks share one validation entrypoint")
    print("  production maintenance scripts require --apply")
    print("  nginx canonicalization and hardening present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
