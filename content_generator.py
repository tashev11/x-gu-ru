"""Safety facade for the legacy x-gu.ru content generator.

The original implementation is preserved verbatim in
``_content_generator_legacy.py``. The facade enforces production invariants
without a risky full rewrite of the legacy renderer.

Key protections:
- canonical template resolution and HTML auto-escaping;
- index policy and whitelist follow the active release through ``current``;
- missing/mismatched policy or whitelist fail closed;
- synthetic review/rating/proof markup is stripped from generated HTML;
- one city morphology implementation is shared across render/repair tools;
- the existing legacy API remains available to callers.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from jinja2 import Environment, FileSystemLoader

try:  # Private-backend package install: app.services.content_generator.
    from .city_morphology import city_prepositional  # type: ignore
except ImportError:  # Public-repository/root execution.
    from city_morphology import city_prepositional

try:  # Private-backend package install.
    from . import _content_generator_legacy as _legacy  # type: ignore
except ImportError:  # Public-repository/root execution.
    import _content_generator_legacy as _legacy  # type: ignore


for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_legacy, _name))


_TRUE_VALUES = {"1", "true", "yes", "on"}
_TEMPLATE_NAMES = {
    "landing_master.html.j2",
    "city_hub_master.html.j2",
    "homepage_master.html.j2",
}
_RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"
_RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"
_DEFAULT_CURRENT_ROOT = Path("/var/www/x-gu.ru/current")

_ORIGINAL_RENDER_LANDING = _legacy._render_html_landing
_ORIGINAL_RENDER_CITY_HUB = _legacy._render_city_hub_html


def _env_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES


def _candidate_repo_roots() -> list[Path]:
    here = Path(__file__).resolve()
    candidates = [here.parent]
    if len(here.parents) >= 3:
        candidates.append(here.parents[2])
    candidates.append(Path.cwd())

    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def _candidate_template_dirs() -> list[Path]:
    candidates: list[Path] = []
    explicit = os.getenv("XGU_TEMPLATE_DIR", "").strip()
    if explicit:
        candidates.append(Path(explicit))

    for root in _candidate_repo_roots():
        candidates.append(root / "server-opt" / "templates")
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
    checked = ", ".join(str(path) for path in _candidate_template_dirs())
    raise RuntimeError(f"No complete x-gu.ru template directory found. Checked: {checked}")


def _template_env() -> Environment:
    return Environment(
        loader=FileSystemLoader(str(_resolve_template_dir())),
        autoescape=True,
    )


def _data_file(name: str) -> Path:
    for root in _candidate_repo_roots():
        candidate = root / "data" / name
        if candidate.exists():
            return candidate.resolve()
    return (_candidate_repo_roots()[0] / "data" / name).resolve()


def _current_root() -> Path:
    return Path(os.getenv("XGU_CURRENT_ROOT", str(_DEFAULT_CURRENT_ROOT))).resolve()


def _release_keep_config_path() -> Path:
    explicit = os.getenv("XGU_KEEP_CONFIG", "").strip()
    if explicit:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise RuntimeError(f"XGU_KEEP_CONFIG points to a missing file: {path}")
        return path

    release_manifest = _current_root() / _RELEASE_KEEP_FILENAME
    if release_manifest.is_file():
        return release_manifest

    if _env_true("XGU_ALLOW_LEGACY_KEEP_CONFIG"):
        legacy = _data_file("index_keep_config.json")
        if legacy.is_file():
            return legacy
    return release_manifest


def _release_whitelist_path(keep_path: Path | None = None) -> Path:
    explicit = os.getenv("XGU_WHITELIST", "").strip()
    if explicit:
        path = Path(explicit).resolve()
        if not path.is_file():
            raise RuntimeError(f"XGU_WHITELIST points to a missing file: {path}")
        return path

    if keep_path is not None and keep_path.name == _RELEASE_KEEP_FILENAME:
        sibling = keep_path.parent / _RELEASE_WHITELIST_FILENAME
        if sibling.is_file():
            return sibling
        return sibling

    release_whitelist = _current_root() / _RELEASE_WHITELIST_FILENAME
    if release_whitelist.is_file():
        return release_whitelist

    if _env_true("XGU_ALLOW_LEGACY_WHITELIST"):
        legacy = _data_file("whitelist.txt")
        if legacy.is_file():
            return legacy
    return release_whitelist


def _whitelist_paths(whitelist: Path) -> set[tuple[str, str | None]]:
    if not whitelist.is_file():
        raise RuntimeError(f"Required whitelist is missing: {whitelist}")

    paths: set[tuple[str, str | None]] = set()
    for line_number, line in enumerate(whitelist.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != "x-gu.ru":
            raise RuntimeError(f"Invalid whitelist URL on line {line_number}: {value}")
        if parsed.query or parsed.fragment:
            raise RuntimeError(f"Whitelist query/fragment is not allowed on line {line_number}: {value}")
        decoded_parts = [unquote(part) for part in parsed.path.split("/") if part]
        if any(part in {".", ".."} or "/" in part or "\\" in part for part in decoded_parts):
            raise RuntimeError(f"Unsafe whitelist path on line {line_number}: {value}")
        if len(decoded_parts) == 1:
            paths.add((decoded_parts[0], None))
        elif len(decoded_parts) == 2:
            paths.add((decoded_parts[0], decoded_parts[1]))
        elif len(decoded_parts) > 2:
            raise RuntimeError(f"Unsupported whitelist path depth on line {line_number}: {value}")
    return paths


def _load_keep_config() -> dict | None:
    """Load release-bound policy + whitelist and fail closed if inconsistent."""
    path = _release_keep_config_path()
    if not path.is_file():
        if _env_true("XGU_ALLOW_MISSING_KEEP_CONFIG"):
            return None
        raise RuntimeError(
            f"Index keep-config is missing: {path}. Refusing to treat generated pages as indexable. "
            "Use XGU_ALLOW_MISSING_KEEP_CONFIG=1 only for an intentional migration/local run."
        )

    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Index keep-config is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"Index keep-config root must be an object: {path}")

    open_cities = set(payload.get("open_cities") or [])
    open_services = set(payload.get("open_services") or [])
    if not open_cities or not open_services:
        raise RuntimeError(f"Index keep-config has empty open_cities/open_services: {path}")

    legacy_keep = path.name == "index_keep_config.json" and _env_true("XGU_ALLOW_LEGACY_KEEP_CONFIG")
    whitelist = _release_whitelist_path(path)

    if not legacy_keep:
        source = str(payload.get("policy_source") or "").strip()
        digest = str(payload.get("policy_sha256") or "").strip().lower()
        whitelist_source = str(payload.get("whitelist_source") or "").strip()
        whitelist_digest = str(payload.get("whitelist_sha256") or "").strip().lower()
        if not source:
            raise RuntimeError(f"Release keep-config has no policy_source: {path}")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise RuntimeError(f"Release keep-config has invalid policy_sha256: {path}")
        if not whitelist_source:
            raise RuntimeError(f"Release keep-config has no whitelist_source: {path}")
        if len(whitelist_digest) != 64 or any(char not in "0123456789abcdef" for char in whitelist_digest):
            raise RuntimeError(f"Release keep-config has invalid whitelist_sha256: {path}")
        if not whitelist.is_file():
            raise RuntimeError(f"Release whitelist is missing: {whitelist}")
        actual_whitelist_digest = hashlib.sha256(whitelist.read_bytes()).hexdigest()
        if actual_whitelist_digest != whitelist_digest:
            raise RuntimeError(
                f"Release whitelist SHA-256 mismatch: manifest={whitelist_digest} actual={actual_whitelist_digest}"
            )
    else:
        if not _env_true("XGU_ALLOW_LEGACY_WHITELIST"):
            raise RuntimeError(
                "Legacy keep-config requires XGU_ALLOW_LEGACY_WHITELIST=1 as a separate migration opt-in"
            )

    return {
        "open_cities": open_cities,
        "open_services": open_services,
        "whitelist_paths": _whitelist_paths(whitelist),
        "policy_source": payload.get("policy_source"),
        "policy_sha256": payload.get("policy_sha256"),
        "whitelist_source": payload.get("whitelist_source"),
        "whitelist_sha256": payload.get("whitelist_sha256"),
        "path": path,
        "whitelist_path": whitelist,
    }


def _page_is_open(city_slug: str, service_slug: str | None = None) -> bool:
    keep = _load_keep_config()
    if keep is None:
        return True
    if (city_slug, service_slug) in keep["whitelist_paths"]:
        return True
    if city_slug not in keep["open_cities"]:
        return False
    return service_slug is None or service_slug in keep["open_services"]


def _clean_jsonld(value):
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
    html = _sanitize_jsonld(html)
    html = _remove_reviews_section(html)
    html = _remove_section_containing(html, "рост органики")
    html = _remove_synthetic_counter_panel(html)

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


_legacy._template_env = _template_env
_legacy._load_keep_config = _load_keep_config
_legacy._page_is_open = _page_is_open
_legacy._city_prepositional = city_prepositional
_legacy._render_html_landing = _render_html_landing
_legacy._render_city_hub_html = _render_city_hub_html

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
