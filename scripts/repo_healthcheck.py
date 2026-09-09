#!/usr/bin/env python3
"""Repository-level safety checks for x-gu.ru.

These checks intentionally avoid importing the private ``app.*`` backend, so
GitHub Actions can validate the public repository on every push/PR.
"""
from __future__ import annotations

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
            require(
                root_copy.read_bytes() == prod_copy.read_bytes(),
                f"template copies diverged: {name}",
                failures,
            )

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
    require(facade_path.is_file(), "content generator facade missing", failures)
    require(legacy_path.is_file(), "preserved legacy generator missing", failures)
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


def check_write_safety(failures: list[str]) -> None:
    for rel_path in WRITE_TO_PRODUCTION_SCRIPTS:
        path = ROOT / rel_path
        require(path.is_file(), f"maintenance script missing: {rel_path}", failures)
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        require("--apply" in text, f"{rel_path}: production writes are not gated by --apply", failures)


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
    require((ROOT / ".github/workflows/ci.yml").is_file(), "CI workflow missing", failures)
    require((ROOT / "requirements.txt").is_file(), "requirements.txt missing", failures)
    require((ROOT / "server-opt/index_policy.example.json").is_file(), "index policy example missing", failures)


def main() -> int:
    failures: list[str] = []
    check_templates(failures)
    check_generator(failures)
    check_write_safety(failures)
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
    print("  production maintenance scripts require --apply")
    print("  canonical robots/template fixes present")
    print("  nginx canonicalization and hardening present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
