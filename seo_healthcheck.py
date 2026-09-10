#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from index_policy import normalize_policy_payload, page_is_open as policy_page_is_open


RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"
RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"
LEGACY_KEEP_CONFIG = Path("/opt/p3-app/data/index_keep_config.json")
LEGACY_WHITELIST = Path("/opt/p3-app/data/whitelist.txt")


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return default if raw in {None, ""} else int(raw)


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw in {None, ""}:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    parsed = urlparse(value)
    clean = parsed._replace(query="", fragment="").geturl()
    parsed = urlparse(clean)
    if parsed.path.endswith("/") or "." in Path(parsed.path).name:
        return clean
    return clean + "/"


class PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description = ""
        self.canonical = ""
        self.lang = ""
        self.h1s: list[str] = []
        self.hrefs: list[str] = []
        self._h1_parts: list[str] | None = None
        self._in_title = False
        self._skip_depth = 0
        self.body_parts: list[str] = []
        self.has_og_title = False
        self.has_og_description = False
        self.has_jsonld = False
        self.has_noindex = False

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {str(key).lower(): (value or "") for key, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        amap = self._attrs(attrs)
        if tag == "html":
            self.lang = amap.get("lang", self.lang)
        elif tag == "title":
            self._in_title = True
        elif tag == "h1":
            self._h1_parts = []
        elif tag in {"script", "style"}:
            if tag == "script" and amap.get("type", "").lower() == "application/ld+json":
                self.has_jsonld = True
            self._skip_depth += 1
        elif tag == "meta":
            name = amap.get("name", "").lower()
            prop = amap.get("property", "").lower()
            content = amap.get("content", "").strip()
            if name == "description" and not self.description:
                self.description = content
            if name == "robots" and "noindex" in content.lower():
                self.has_noindex = True
            if prop == "og:title":
                self.has_og_title = True
            if prop == "og:description":
                self.has_og_description = True
        elif tag == "link":
            rel = {part.lower() for part in amap.get("rel", "").split()}
            if "canonical" in rel and not self.canonical:
                self.canonical = amap.get("href", "").strip()
        elif tag == "a":
            href = amap.get("href", "").strip()
            if href:
                self.hrefs.append(href)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._h1_parts is not None:
            self.h1s.append(" ".join(self._h1_parts).strip())
            self._h1_parts = None
        elif tag in {"script", "style"} and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title_parts.append(text)
        if self._h1_parts is not None:
            self._h1_parts.append(text)
        if self._skip_depth == 0:
            self.body_parts.append(text)

    @property
    def title(self) -> str:
        return unescape(" ".join(self.title_parts).strip())

    @property
    def body_text(self) -> str:
        return unescape(" ".join(self.body_parts).strip())


def _page_url(root: Path, html_file: Path, base_url: str) -> str:
    rel = html_file.parent.relative_to(root)
    if str(rel) == ".":
        return base_url + "/"
    return f"{base_url}/{rel.as_posix()}/"


def _read_sitemap_urls(root: Path, base_url: str) -> tuple[set[str], int]:
    """Return page URLs only; sitemap-index shard locs are metadata."""
    urls: set[str] = set()
    invalid = 0
    base = urlparse(base_url)
    loc_re = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
    for xml in root.rglob("sitemap*.xml"):
        try:
            text = xml.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            invalid += 1
            continue
        for value in loc_re.findall(text):
            raw = unescape(value.strip())
            parsed = urlparse(raw)
            if parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.query or parsed.fragment:
                invalid += 1
                continue
            if parsed.path.endswith(".xml"):
                continue
            urls.add(_normalize_url(raw))
    return urls, invalid


def _invalid_jsonld_blocks(html: str) -> int:
    blocks = re.findall(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html,
        flags=re.I | re.S,
    )
    invalid = 0
    for block in blocks:
        try:
            json.loads(block.strip())
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid += 1
    return invalid


def _internal_page_target(href: str, current_url: str, base_url: str) -> str | None:
    href = href.strip()
    lowered = href.lower()
    if not href or lowered.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None

    parsed = urlparse(urljoin(current_url, href))
    base = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != base.netloc:
        return None
    path = parsed.path or "/"
    if path.startswith(("/api/", "/assets/", "/.well-known/", "/console")):
        return None
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
    if "." in Path(path).name:
        return None
    return _normalize_url(f"{base_url}{path}")


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _default_keep_config(root: Path) -> Path:
    explicit = os.getenv("SEOHC_KEEP_CONFIG", "").strip()
    if explicit:
        return Path(explicit).resolve()
    release_manifest = root.resolve() / RELEASE_KEEP_FILENAME
    if release_manifest.is_file():
        return release_manifest
    if _as_bool("SEOHC_ALLOW_LEGACY_KEEP_CONFIG", False) and LEGACY_KEEP_CONFIG.is_file():
        return LEGACY_KEEP_CONFIG
    return release_manifest


def _default_whitelist(root: Path, keep_config: Path) -> Path:
    explicit = os.getenv("SEOHC_WHITELIST", "").strip()
    if explicit:
        return Path(explicit).resolve()
    if keep_config.name == RELEASE_KEEP_FILENAME:
        return keep_config.parent / RELEASE_WHITELIST_FILENAME
    release_whitelist = root.resolve() / RELEASE_WHITELIST_FILENAME
    if release_whitelist.is_file():
        return release_whitelist
    if _as_bool("SEOHC_ALLOW_LEGACY_WHITELIST", False) and LEGACY_WHITELIST.is_file():
        return LEGACY_WHITELIST
    return release_whitelist


def _load_index_policy(keep_config: Path, whitelist: Path, base_url: str) -> dict | None:
    if not keep_config.is_file() or not whitelist.is_file():
        return None

    payload = json.loads(keep_config.read_text(encoding="utf-8", errors="strict"))
    if not isinstance(payload, dict):
        raise ValueError("index keep-config root must be a JSON object")
    policy = normalize_policy_payload(payload)

    if keep_config.name == RELEASE_KEEP_FILENAME:
        if whitelist.name != RELEASE_WHITELIST_FILENAME or whitelist.parent.resolve() != keep_config.parent.resolve():
            raise ValueError("release policy must use the sibling release whitelist snapshot")
        if not str(payload.get("policy_source") or "").strip() or not _valid_sha256(payload.get("policy_sha256")):
            raise ValueError("release policy provenance is invalid")
        if not str(payload.get("whitelist_source") or "").strip() or not _valid_sha256(payload.get("whitelist_sha256")):
            raise ValueError("release whitelist provenance is invalid")
        expected = str(payload.get("whitelist_sha256")).strip().lower()
        actual = hashlib.sha256(whitelist.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"release whitelist SHA-256 mismatch: manifest={expected} actual={actual}")

    whitelist_urls: set[str] = set()
    base = urlparse(base_url)
    for line_number, line in enumerate(whitelist.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        if value.startswith("/"):
            value = base_url + value
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.query or parsed.fragment:
            raise ValueError(f"invalid whitelist URL on line {line_number}: {value}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) > 2:
            raise ValueError(f"unsupported whitelist path depth on line {line_number}: {value}")
        whitelist_urls.add(_normalize_url(value))

    return {
        **policy,
        "open_cities": set(policy["open_cities"]),
        "open_services": set(policy["open_services"]),
        "open_pairs": set(policy["open_pairs"]),
        "whitelist_urls": whitelist_urls,
    }


def _expected_indexable(page_url: str, base_url: str, policy: dict | None) -> bool | None:
    if policy is None:
        return None
    page_url = _normalize_url(page_url)
    if page_url in policy["whitelist_urls"]:
        return True
    parts = [part for part in urlparse(page_url).path.split("/") if part]
    if not parts or parts == ["privacy"]:
        return True
    if len(parts) == 1:
        return policy_page_is_open(policy, parts[0], None)
    if len(parts) == 2:
        return policy_page_is_open(policy, parts[0], parts[1])
    return False


def run_audit(
    root: Path,
    *,
    base_url: str | None = None,
    keep_config: Path | None = None,
    whitelist: Path | None = None,
) -> dict:
    root = root.resolve()
    base_url = _normalize_base_url(base_url or os.getenv("SEOHC_BASE_URL", "https://x-gu.ru"))
    keep_config = (keep_config or _default_keep_config(root)).resolve()
    whitelist = (whitelist or _default_whitelist(root, keep_config)).resolve()

    files = list(root.rglob("index.html"))
    page_urls = {_normalize_url(_page_url(root, html_file, base_url)) for html_file in files}
    sitemap_urls, bad_sitemap_urls = _read_sitemap_urls(root, base_url)
    policy_error = ""
    try:
        policy = _load_index_policy(keep_config, whitelist, base_url)
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        policy = None
        policy_error = str(exc)

    stats = {
        "pages_total": len(files),
        "missing_title": 0,
        "missing_description": 0,
        "missing_canonical": 0,
        "missing_h1": 0,
        "multi_h1": 0,
        "missing_lang_ru": 0,
        "missing_og_title": 0,
        "missing_og_description": 0,
        "missing_jsonld": 0,
        "invalid_jsonld_pages": 0,
        "invalid_jsonld_blocks": 0,
        "has_noindex": 0,
        "thin_content_lt_250_words": 0,
        "bad_title_len": 0,
        "bad_description_len": 0,
        "bad_canonical_domain": 0,
        "canonical_url_mismatch": 0,
        "unexpected_noindex_open": 0,
        "unexpected_index_closed": 0,
        "open_missing_sitemap": 0,
        "closed_in_sitemap": 0,
        "bad_sitemap_urls": bad_sitemap_urls,
        "sitemap_orphan_urls": len(sitemap_urls - page_urls),
        "broken_internal_links": 0,
        "pages_with_broken_internal_links": 0,
        "policy_checked_pages": 0,
    }
    title_counter: Counter[str] = Counter()
    h1_counter: Counter[str] = Counter()
    canonical_counter: Counter[str] = Counter()
    page_links: list[tuple[str, list[str]]] = []

    for html_file in files:
        html = html_file.read_text(encoding="utf-8", errors="ignore")
        parser = PageParser()
        try:
            parser.feed(html)
        except Exception:
            pass

        title = parser.title
        desc = parser.description.strip()
        canon = parser.canonical.strip()
        h1s = [" ".join(value.split()) for value in parser.h1s if value.strip()]
        page_url = _page_url(root, html_file, base_url)
        normalized_page_url = _normalize_url(page_url)
        normalized_canon = _normalize_url(canon) if canon else ""
        expected_open = _expected_indexable(page_url, base_url, policy)
        invalid_jsonld = _invalid_jsonld_blocks(html)
        page_links.append((normalized_page_url, parser.hrefs))

        words = [word for word in re.split(r"[^\wа-яё-]+", parser.body_text.lower(), flags=re.I) if word]
        if title:
            title_counter[title] += 1
        if h1s:
            h1_counter[h1s[0]] += 1
        if normalized_canon:
            canonical_counter[normalized_canon] += 1

        if not title:
            stats["missing_title"] += 1
        if not desc:
            stats["missing_description"] += 1
        if not canon:
            stats["missing_canonical"] += 1
        if not h1s:
            stats["missing_h1"] += 1
        if len(h1s) > 1:
            stats["multi_h1"] += 1
        if not parser.lang.lower().startswith("ru"):
            stats["missing_lang_ru"] += 1
        if not parser.has_og_title:
            stats["missing_og_title"] += 1
        if not parser.has_og_description:
            stats["missing_og_description"] += 1
        if not parser.has_jsonld:
            stats["missing_jsonld"] += 1
        if invalid_jsonld:
            stats["invalid_jsonld_pages"] += 1
            stats["invalid_jsonld_blocks"] += invalid_jsonld
        if parser.has_noindex:
            stats["has_noindex"] += 1
        if len(words) < 250:
            stats["thin_content_lt_250_words"] += 1
        if title and not (30 <= len(title) <= 75):
            stats["bad_title_len"] += 1
        if desc and not (90 <= len(desc) <= 180):
            stats["bad_description_len"] += 1
        if canon and not canon.startswith(base_url + "/"):
            stats["bad_canonical_domain"] += 1
        if canon and normalized_canon != normalized_page_url:
            stats["canonical_url_mismatch"] += 1

        if expected_open is not None:
            stats["policy_checked_pages"] += 1
            if expected_open:
                if parser.has_noindex:
                    stats["unexpected_noindex_open"] += 1
                if normalized_page_url not in sitemap_urls:
                    stats["open_missing_sitemap"] += 1
            else:
                if not parser.has_noindex:
                    stats["unexpected_index_closed"] += 1
                if normalized_page_url in sitemap_urls:
                    stats["closed_in_sitemap"] += 1

    for current_url, hrefs in page_links:
        broken_targets: set[str] = set()
        for href in hrefs:
            target = _internal_page_target(href, current_url, base_url)
            if target is not None and target not in page_urls:
                broken_targets.add(target)
        if broken_targets:
            stats["pages_with_broken_internal_links"] += 1
            stats["broken_internal_links"] += len(broken_targets)

    duplicates = {
        "title_duplicate_pages": sum(value for value in title_counter.values() if value > 1),
        "title_duplicate_groups": sum(1 for value in title_counter.values() if value > 1),
        "h1_duplicate_pages": sum(value for value in h1_counter.values() if value > 1),
        "h1_duplicate_groups": sum(1 for value in h1_counter.values() if value > 1),
        "canonical_duplicate_pages": sum(value for value in canonical_counter.values() if value > 1),
        "canonical_duplicate_groups": sum(1 for value in canonical_counter.values() if value > 1),
    }
    return {
        "stats": stats,
        "duplicates": duplicates,
        "policy_loaded": policy is not None,
        "policy_version": policy.get("policy_version") if policy else None,
        "policy_mode": policy.get("policy_mode") if policy else None,
        "policy_error": policy_error,
        "keep_config": str(keep_config),
        "whitelist": str(whitelist),
        "base_url": base_url,
        "sitemap_urls": len(sitemap_urls),
    }


def evaluate(audit: dict) -> tuple[bool, list[str]]:
    stats = audit["stats"]
    duplicates = audit["duplicates"]
    limits = {
        "missing_title": _as_int("SEOHC_MAX_MISSING_TITLE", 0),
        "missing_description": _as_int("SEOHC_MAX_MISSING_DESCRIPTION", 0),
        "missing_canonical": _as_int("SEOHC_MAX_MISSING_CANONICAL", 0),
        "missing_h1": _as_int("SEOHC_MAX_MISSING_H1", 0),
        "missing_og_title": _as_int("SEOHC_MAX_MISSING_OG_TITLE", 0),
        "missing_og_description": _as_int("SEOHC_MAX_MISSING_OG_DESCRIPTION", 0),
        "missing_jsonld": _as_int("SEOHC_MAX_MISSING_JSONLD", 0),
        "invalid_jsonld_pages": _as_int("SEOHC_MAX_INVALID_JSONLD_PAGES", 0),
        "bad_title_len": _as_int("SEOHC_MAX_BAD_TITLE_LEN", 0),
        "bad_description_len": _as_int("SEOHC_MAX_BAD_DESC_LEN", 0),
        "bad_canonical_domain": _as_int("SEOHC_MAX_BAD_CANONICAL_DOMAIN", 0),
        "canonical_url_mismatch": _as_int("SEOHC_MAX_CANONICAL_MISMATCH", 0),
        "unexpected_noindex_open": _as_int("SEOHC_MAX_UNEXPECTED_NOINDEX_OPEN", 0),
        "unexpected_index_closed": _as_int("SEOHC_MAX_UNEXPECTED_INDEX_CLOSED", 0),
        "open_missing_sitemap": _as_int("SEOHC_MAX_OPEN_MISSING_SITEMAP", 0),
        "closed_in_sitemap": _as_int("SEOHC_MAX_CLOSED_IN_SITEMAP", 0),
        "bad_sitemap_urls": _as_int("SEOHC_MAX_BAD_SITEMAP_URLS", 0),
        "sitemap_orphan_urls": _as_int("SEOHC_MAX_SITEMAP_ORPHAN_URLS", 0),
        "broken_internal_links": _as_int("SEOHC_MAX_BROKEN_INTERNAL_LINKS", 0),
        "title_duplicate_pages": _as_int("SEOHC_MAX_TITLE_DUP_PAGES", 0),
        "h1_duplicate_pages": _as_int("SEOHC_MAX_H1_DUP_PAGES", 0),
        "canonical_duplicate_pages": _as_int("SEOHC_MAX_CANONICAL_DUP_PAGES", 0),
    }

    breaches: list[str] = []
    if _as_bool("SEOHC_REQUIRE_POLICY", True) and not audit.get("policy_loaded"):
        detail = audit.get("policy_error") or audit.get("keep_config")
        breaches.append(f"policy_not_loaded: {detail}")

    for key in (
        "missing_title",
        "missing_description",
        "missing_canonical",
        "missing_h1",
        "missing_og_title",
        "missing_og_description",
        "missing_jsonld",
        "invalid_jsonld_pages",
        "bad_title_len",
        "bad_description_len",
        "bad_canonical_domain",
        "canonical_url_mismatch",
        "unexpected_noindex_open",
        "unexpected_index_closed",
        "open_missing_sitemap",
        "closed_in_sitemap",
        "bad_sitemap_urls",
        "sitemap_orphan_urls",
        "broken_internal_links",
    ):
        if stats[key] > limits[key]:
            breaches.append(f"{key}={stats[key]} > {limits[key]}")

    for key in ("title_duplicate_pages", "h1_duplicate_pages", "canonical_duplicate_pages"):
        if duplicates[key] > limits[key]:
            breaches.append(f"{key}={duplicates[key]} > {limits[key]}")
    return len(breaches) == 0, breaches


def _send_telegram_if_available(text: str) -> None:
    try:
        from app.services.notify_service import send_telegram
    except ImportError:
        print("SEOHC notification skipped: private app.services.notify_service is unavailable")
        return
    send_telegram(text)


def main() -> int:
    root = Path(os.getenv("SEOHC_ROOT", "/var/www/x-gu.ru/current"))
    if not root.exists():
        raise SystemExit(f"SEOHC_ROOT not found: {root}")

    audit = run_audit(root)
    ok, breaches = evaluate(audit)
    stats = audit["stats"]
    duplicates = audit["duplicates"]
    status = "OK" if ok else "WARN"

    text = (
        f"SEO Healthcheck [{status}]\n"
        f"Root: {root}\n"
        f"Base: {audit['base_url']}\n"
        f"Policy: {audit['keep_config']} ({'loaded' if audit['policy_loaded'] else 'not loaded'}) "
        f"version={audit.get('policy_version')} mode={audit.get('policy_mode')}\n"
        f"Whitelist: {audit['whitelist']}\n"
        f"Pages: {stats['pages_total']}; sitemap page URLs: {audit['sitemap_urls']}\n"
        f"missing_title={stats['missing_title']}, missing_description={stats['missing_description']}, "
        f"missing_canonical={stats['missing_canonical']}, missing_h1={stats['missing_h1']}\n"
        f"bad_title_len={stats['bad_title_len']}, bad_description_len={stats['bad_description_len']}, "
        f"canonical_mismatch={stats['canonical_url_mismatch']}, canonical_dup_pages={duplicates['canonical_duplicate_pages']}\n"
        f"jsonld_missing={stats['missing_jsonld']}, jsonld_invalid_pages={stats['invalid_jsonld_pages']}\n"
        f"noindex_total={stats['has_noindex']}, unexpected_noindex_open={stats['unexpected_noindex_open']}, "
        f"unexpected_index_closed={stats['unexpected_index_closed']}\n"
        f"open_missing_sitemap={stats['open_missing_sitemap']}, closed_in_sitemap={stats['closed_in_sitemap']}, "
        f"bad_sitemap_urls={stats['bad_sitemap_urls']}, sitemap_orphans={stats['sitemap_orphan_urls']}, "
        f"broken_internal_links={stats['broken_internal_links']}\n"
        f"title_dup_pages={duplicates['title_duplicate_pages']}, h1_dup_pages={duplicates['h1_duplicate_pages']}"
    )
    if breaches:
        text += "\nBreaches: " + "; ".join(breaches[:20])

    print(text)
    notify_enabled = _as_bool("SEOHC_NOTIFY_ENABLED", False)
    notify_on_ok = _as_bool("SEOHC_NOTIFY_ON_OK", False)
    if notify_enabled and (notify_on_ok or not ok):
        _send_telegram_if_available(text)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
