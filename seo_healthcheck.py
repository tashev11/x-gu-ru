#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
from collections import Counter
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


def _as_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def _normalize_url(value: str) -> str:
    value = value.strip()
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.path.endswith("/") or "." in Path(parsed.path).name:
        return value
    return value + "/"


class PageParser(HTMLParser):
    """Small HTML parser for the fields the healthcheck needs.

    This avoids fragile assumptions about attribute order in meta/link tags.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.description = ""
        self.canonical = ""
        self.lang = ""
        self.h1s: list[str] = []
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
        return {str(k).lower(): (v or "") for k, v in attrs}

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

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        elif tag == "h1" and self._h1_parts is not None:
            value = " ".join(self._h1_parts).strip()
            self.h1s.append(value)
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


def _read_sitemap_urls(root: Path) -> set[str]:
    urls: set[str] = set()
    loc_re = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)
    for xml in root.rglob("sitemap*.xml"):
        try:
            text = xml.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for value in loc_re.findall(text):
            urls.add(_normalize_url(unescape(value.strip())))
    return urls


def _load_index_policy(keep_config: Path, whitelist: Path, base_url: str) -> dict | None:
    if not keep_config.is_file():
        return None

    payload = json.loads(keep_config.read_text(encoding="utf-8"))
    open_cities = set(payload.get("open_cities") or [])
    open_services = set(payload.get("open_services") or [])
    whitelist_urls: set[str] = set()

    if whitelist.is_file():
        for line in whitelist.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if not value:
                continue
            if value.startswith("/"):
                value = base_url + value
            whitelist_urls.add(_normalize_url(value))

    return {
        "open_cities": open_cities,
        "open_services": open_services,
        "whitelist_urls": whitelist_urls,
    }


def _expected_indexable(page_url: str, base_url: str, policy: dict | None) -> bool | None:
    if policy is None:
        return None
    page_url = _normalize_url(page_url)
    if page_url in policy["whitelist_urls"]:
        return True

    path = urlparse(page_url).path
    parts = [part for part in path.split("/") if part]
    if not parts or parts == ["privacy"]:
        return True
    if len(parts) == 1:
        return parts[0] in policy["open_cities"]
    if len(parts) == 2:
        return parts[0] in policy["open_cities"] and parts[1] in policy["open_services"]
    return False


def run_audit(
    root: Path,
    *,
    base_url: str | None = None,
    keep_config: Path | None = None,
    whitelist: Path | None = None,
) -> dict:
    base_url = _normalize_base_url(base_url or os.getenv("SEOHC_BASE_URL", "https://x-gu.ru"))
    keep_config = keep_config or Path(os.getenv("SEOHC_KEEP_CONFIG", "/opt/p3-app/data/index_keep_config.json"))
    whitelist = whitelist or Path(os.getenv("SEOHC_WHITELIST", "/opt/p3-app/data/whitelist.txt"))

    files = list(root.rglob("index.html"))
    sitemap_urls = _read_sitemap_urls(root)
    policy = _load_index_policy(keep_config, whitelist, base_url)

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
        "policy_checked_pages": 0,
    }
    title_counter: Counter[str] = Counter()
    h1_counter: Counter[str] = Counter()

    for html_file in files:
        html = html_file.read_text(encoding="utf-8", errors="ignore")
        parser = PageParser()
        try:
            parser.feed(html)
        except Exception:
            # Continue with whatever the tolerant HTMLParser managed to collect.
            pass

        title = parser.title
        desc = parser.description.strip()
        canon = parser.canonical.strip()
        h1s = [" ".join(value.split()) for value in parser.h1s if value.strip()]
        page_url = _page_url(root, html_file, base_url)
        normalized_page_url = _normalize_url(page_url)
        normalized_canon = _normalize_url(canon) if canon else ""
        expected_open = _expected_indexable(page_url, base_url, policy)

        words = [w for w in re.split(r"[^\wа-яё-]+", parser.body_text.lower(), flags=re.I) if w]

        if title:
            title_counter[title] += 1
        if h1s:
            h1_counter[h1s[0]] += 1

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

    duplicates = {
        "title_duplicate_pages": sum(v for v in title_counter.values() if v > 1),
        "title_duplicate_groups": sum(1 for v in title_counter.values() if v > 1),
        "h1_duplicate_pages": sum(v for v in h1_counter.values() if v > 1),
        "h1_duplicate_groups": sum(1 for v in h1_counter.values() if v > 1),
    }
    return {
        "stats": stats,
        "duplicates": duplicates,
        "policy_loaded": policy is not None,
        "base_url": base_url,
        "sitemap_urls": len(sitemap_urls),
    }


def evaluate(audit: dict) -> tuple[bool, list[str]]:
    s = audit["stats"]
    d = audit["duplicates"]
    limits = {
        "missing_title": _as_int("SEOHC_MAX_MISSING_TITLE", 0),
        "missing_description": _as_int("SEOHC_MAX_MISSING_DESCRIPTION", 0),
        "missing_canonical": _as_int("SEOHC_MAX_MISSING_CANONICAL", 0),
        "missing_h1": _as_int("SEOHC_MAX_MISSING_H1", 0),
        "missing_og_title": _as_int("SEOHC_MAX_MISSING_OG_TITLE", 0),
        "missing_og_description": _as_int("SEOHC_MAX_MISSING_OG_DESCRIPTION", 0),
        "missing_jsonld": _as_int("SEOHC_MAX_MISSING_JSONLD", 0),
        "bad_title_len": _as_int("SEOHC_MAX_BAD_TITLE_LEN", 0),
        "bad_description_len": _as_int("SEOHC_MAX_BAD_DESC_LEN", 0),
        "bad_canonical_domain": _as_int("SEOHC_MAX_BAD_CANONICAL_DOMAIN", 0),
        "canonical_url_mismatch": _as_int("SEOHC_MAX_CANONICAL_MISMATCH", 0),
        "unexpected_noindex_open": _as_int("SEOHC_MAX_UNEXPECTED_NOINDEX_OPEN", 0),
        "unexpected_index_closed": _as_int("SEOHC_MAX_UNEXPECTED_INDEX_CLOSED", 0),
        "open_missing_sitemap": _as_int("SEOHC_MAX_OPEN_MISSING_SITEMAP", 0),
        "closed_in_sitemap": _as_int("SEOHC_MAX_CLOSED_IN_SITEMAP", 0),
        "title_duplicate_pages": _as_int("SEOHC_MAX_TITLE_DUP_PAGES", 0),
        "h1_duplicate_pages": _as_int("SEOHC_MAX_H1_DUP_PAGES", 0),
    }

    breaches: list[str] = []
    for key in (
        "missing_title",
        "missing_description",
        "missing_canonical",
        "missing_h1",
        "missing_og_title",
        "missing_og_description",
        "missing_jsonld",
        "bad_title_len",
        "bad_description_len",
        "bad_canonical_domain",
        "canonical_url_mismatch",
        "unexpected_noindex_open",
        "unexpected_index_closed",
        "open_missing_sitemap",
        "closed_in_sitemap",
    ):
        if s[key] > limits[key]:
            breaches.append(f"{key}={s[key]} > {limits[key]}")

    if d["title_duplicate_pages"] > limits["title_duplicate_pages"]:
        breaches.append(
            f"title_duplicate_pages={d['title_duplicate_pages']} > {limits['title_duplicate_pages']}"
        )
    if d["h1_duplicate_pages"] > limits["h1_duplicate_pages"]:
        breaches.append(f"h1_duplicate_pages={d['h1_duplicate_pages']} > {limits['h1_duplicate_pages']}")
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
    s = audit["stats"]
    d = audit["duplicates"]
    status = "OK" if ok else "WARN"

    text = (
        f"SEO Healthcheck [{status}]\n"
        f"Root: {root}\n"
        f"Base: {audit['base_url']}\n"
        f"Pages: {s['pages_total']}; sitemap URLs: {audit['sitemap_urls']}; "
        f"policy={'loaded' if audit['policy_loaded'] else 'not loaded'}\n"
        f"missing_title={s['missing_title']}, missing_description={s['missing_description']}, "
        f"missing_canonical={s['missing_canonical']}, missing_h1={s['missing_h1']}\n"
        f"bad_title_len={s['bad_title_len']}, bad_description_len={s['bad_description_len']}, "
        f"canonical_mismatch={s['canonical_url_mismatch']}\n"
        f"noindex_total={s['has_noindex']}, unexpected_noindex_open={s['unexpected_noindex_open']}, "
        f"unexpected_index_closed={s['unexpected_index_closed']}\n"
        f"open_missing_sitemap={s['open_missing_sitemap']}, closed_in_sitemap={s['closed_in_sitemap']}\n"
        f"title_dup_pages={d['title_duplicate_pages']}, h1_dup_pages={d['h1_duplicate_pages']}"
    )
    if breaches:
        text += "\nBreaches: " + "; ".join(breaches[:12])

    print(text)
    notify_enabled = _as_bool("SEOHC_NOTIFY_ENABLED", False)
    notify_on_ok = _as_bool("SEOHC_NOTIFY_ON_OK", False)
    if notify_enabled and (notify_on_ok or not ok):
        _send_telegram_if_available(text)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
