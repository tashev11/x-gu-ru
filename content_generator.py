"""Safety facade for the legacy x-gu.ru content generator.

The original implementation is preserved verbatim in
``_content_generator_legacy.py``. Keeping it intact makes this hardening
change reviewable while allowing the public entry point to enforce production
invariants before/after rendering.

Key protections:
- one resolved template directory and HTML auto-escaping;
- fail closed when the index keep-config disappears;
- strip synthetic review/rating schema and synthetic proof sections from
  generated HTML;
- use one city morphology implementation across render and repair tools;
- keep all existing public/underscore functions available to callers.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

try:  # Private-backend package install: app.services.content_generator.
    from .city_morphology import city_prepositional  # type: ignore
except ImportError:  # Public-repository/root execution.
    from city_morphology import city_prepositional

try:  # Works when this file is installed as app.services.content_generator.
    from . import _content_generator_legacy as _legacy  # type: ignore
except ImportError:  # Works from this public repository root.
    import _content_generator_legacy as _legacy  # type: ignore


# Re-export the legacy surface first. Existing imports such as
# ``from content_generator import _render_html_landing`` keep working.
for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))


_TRUE_VALUES = {"1", "true", "yes", "on"}
_TEMPLATE_NAMES = {
    "landing_master.html.j2",
    "city_hub_master.html.j2",
    "homepage_master.html.j2",
}

_ORIGINAL_LOAD_KEEP_CONFIG = _legacy._load_keep_config
_ORIGINAL_RENDER_LANDING = _legacy._render_html_landing
_ORIGINAL_RENDER_CITY_HUB = _legacy._render_city_hub_html


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES


def _candidate_template_dirs() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.getenv("XGU_TEMPLATE_DIR", "").strip()
    if explicit:
        candidates.append(Path(explicit))

    here = Path(__file__).resolve()

    # Canonical repository templates have priority over legacy app/templates.
    # Public-repo layout: content_generator.py + server-opt/templates.
    candidates.append(here.parent / "server-opt" / "templates")

    # Private-backend layout: app/services/content_generator.py and repo root.
    if len(here.parents) >= 3:
        candidates.append(here.parents[2] / "server-opt" / "templates")

    # Compatibility fallback for older deployments that have not yet moved
    # their templates to the canonical repository directory.
    candidates.append(Path("app/templates"))

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _resolve_template_dir() -> Path:
    for candidate in _candidate_template_dirs():
        if candidate.is_dir() and all((candidate / name).is_file() for name in _TEMPLATE_NAMES):
            return candidate
    checked = ", ".join(str(p) for p in _candidate_template_dirs())
    raise RuntimeError(f"No complete x-gu.ru template directory found. Checked: {checked}")


def _template_env() -> Environment:
    """Return a single, explicit, auto-escaping HTML template environment."""
    return Environment(
        loader=FileSystemLoader(str(_resolve_template_dir())),
        autoescape=True,
    )


def _load_keep_config() -> dict | None:
    """Load index policy and fail closed if production policy disappears.

    Historically a missing ``data/index_keep_config.json`` meant "index every
    generated page". That is too dangerous for a programmatic-SEO site. An
    explicit escape hatch exists only for controlled migrations/local work.
    """
    keep = _ORIGINAL_LOAD_KEEP_CONFIG()
    if keep is None and not _env_true("XGU_ALLOW_MISSING_KEEP_CONFIG"):
        raise RuntimeError(
            "data/index_keep_config.json is missing; refusing to treat every "
            "generated page as indexable. Set XGU_ALLOW_MISSING_KEEP_CONFIG=1 "
            "only for an intentional migration/local run."
        )
    return keep


def _page_is_open(city_slug: str, service_slug: str | None = None) -> bool:
    keep = _load_keep_config()
    if keep is None:  # Only possible via the explicit migration escape hatch.
        return True
    if (city_slug, service_slug) in keep["whitelist_paths"]:
        return True
    if city_slug not in keep["open_cities"]:
        return False
    return service_slug is None or service_slug in keep["open_services"]


def _clean_jsonld(value):
    """Remove ungrounded ratings/reviews recursively from JSON-LD."""
    if isinstance(value, dict):
        cleaned = {}
        for key, item in value.items():
            if key in {"aggregateRating", "review"}:
                continue
            cleaned[key] = _clean_jsonld(item)
        return cleaned
    if isinstance(value, list):
        return [_clean_jsonld(item) for item in value]
    return value


_JSONLD_RE = re.compile(
    r'(<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>)(.*?)(</script>)',
    re.IGNORECASE | re.DOTALL,
)


def _sanitize_jsonld(html: str) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group(2).strip()
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return match.group(0)

        # A generated city/service page is not evidence of a physical office in
        # that city. The separate Organization + Service schema is sufficient.
        if isinstance(payload, dict) and payload.get("@type") == "LocalBusiness":
            return ""

        payload = _clean_jsonld(payload)
        compact = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return f"{match.group(1)}{compact}{match.group(3)}"

    return _JSONLD_RE.sub(repl, html)


def _remove_balanced_from_start(html: str, start: int, tag: str) -> str:
    if start < 0:
        return html
    token_re = re.compile(rf"</?{re.escape(tag)}\b[^>]*>", re.IGNORECASE)
    depth = 0
    for token in token_re.finditer(html, start):
        text = token.group(0).lstrip().lower()
        if text.startswith(f"</{tag.lower()}"):
            depth -= 1
            if depth == 0:
                return html[:start] + html[token.end():]
        else:
            depth += 1
    return html


def _remove_section_containing(html: str, marker: str) -> str:
    pos = html.find(marker)
    if pos < 0:
        return html
    start = html.rfind("<section", 0, pos)
    return _remove_balanced_from_start(html, start, "section")


def _remove_reviews_section(html: str) -> str:
    for marker in ('id="reviews"', "id='reviews'"):
        pos = html.find(marker)
        if pos >= 0:
            start = html.rfind("<section", 0, pos)
            html = _remove_balanced_from_start(html, start, "section")
            break
    # Remove navigation/footer links that would otherwise point to a deleted
    # synthetic-review section.
    html = re.sub(
        r'<a\b[^>]*href=["\']#reviews["\'][^>]*>.*?</a>',
        "",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return html


def _remove_synthetic_counter_panel(html: str) -> str:
    marker = '<div class="hidden lg:block fade-in"'
    start = html.find(marker)
    if start >= 0:
        html = _remove_balanced_from_start(html, start, "div")
    return html


def _sanitize_generated_html(html: str) -> str:
    """Remove generated proof that is not backed by a real data source."""
    html = _sanitize_jsonld(html)
    html = _remove_reviews_section(html)

    # City-hub KPIs were deterministic hash-derived numbers, not analytics.
    html = _remove_section_containing(html, "рост органики")

    # Landing counters are profile-generated marketing numbers rather than
    # measured page/client metrics.
    html = _remove_synthetic_counter_panel(html)

    # Remove/neutralize unsupported blanket proof claims while preserving the
    # actual offer and page layout.
    replacements = {
        "50+ проектов": "Работа по этапам",
        "TOP-10 гарантии": "Прозрачные отчёты",
        "24/7 поддержка": "Связь 9:00–21:00",
        "Экономия до 150 000 рублей!": "Оценим задачу и бюджет до старта.",
        "Экономия до 150 000₽": "Понятный бюджет до старта",
    }
    for old, new in replacements.items():
        html = html.replace(old, new)
    return html


def _render_html_landing(*args, **kwargs) -> str:
    return _sanitize_generated_html(_ORIGINAL_RENDER_LANDING(*args, **kwargs))


def _render_city_hub_html(*args, **kwargs) -> str:
    return _sanitize_generated_html(_ORIGINAL_RENDER_CITY_HUB(*args, **kwargs))


# Patch the legacy module globals too: legacy functions such as
# render_sites_to_hugo resolve these names in their own module namespace.
_legacy._template_env = _template_env
_legacy._load_keep_config = _load_keep_config
_legacy._page_is_open = _page_is_open
_legacy._city_prepositional = city_prepositional
_legacy._render_html_landing = _render_html_landing
_legacy._render_city_hub_html = _render_city_hub_html

# Ensure direct imports from this facade resolve to the hardened functions.
globals().update(
    {
        "_template_env": _template_env,
        "_load_keep_config": _load_keep_config,
        "_page_is_open": _page_is_open,
        "_city_prepositional": city_prepositional,
        "_render_html_landing": _render_html_landing,
        "_render_city_hub_html": _render_city_hub_html,
        "_sanitize_generated_html": _sanitize_generated_html,
    }
)
