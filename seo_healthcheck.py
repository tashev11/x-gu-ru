#!/usr/bin/env python3
from __future__ import annotations

import os
import re
from collections import Counter
from pathlib import Path

from app.services.notify_service import send_telegram


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


def run_audit(root: Path) -> dict:
    files = list(root.rglob("index.html"))
    re_title = re.compile(r"<title>(.*?)</title>", re.I | re.S)
    re_desc = re.compile(r'<meta[^>]+name=["\\\']description["\\\'][^>]+content=["\\\'](.*?)["\\\']', re.I | re.S)
    re_canon = re.compile(r'<link[^>]+rel=["\\\']canonical["\\\'][^>]+href=["\\\'](.*?)["\\\']', re.I | re.S)
    re_h1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
    re_lang = re.compile(r"<html[^>]+lang=[\"\\'](.*?)[\"\\']", re.I | re.S)
    re_og_title = re.compile(r'<meta[^>]+property=["\\\']og:title["\\\']', re.I)
    re_og_desc = re.compile(r'<meta[^>]+property=["\\\']og:description["\\\']', re.I)
    re_jsonld = re.compile(r"<script[^>]+application/ld\+json", re.I)
    re_noindex = re.compile(r"noindex", re.I)
    re_tags = re.compile(r"<[^>]+>")
    re_space = re.compile(r"\s+")

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
    }
    title_counter = Counter()
    h1_counter = Counter()

    for f in files:
        html = f.read_text(encoding="utf-8", errors="ignore")
        m_title = re_title.search(html)
        title = re_space.sub(" ", re_tags.sub("", m_title.group(1))).strip() if m_title else ""
        m_desc = re_desc.search(html)
        desc = re_space.sub(" ", re_tags.sub("", m_desc.group(1))).strip() if m_desc else ""
        m_canon = re_canon.search(html)
        canon = m_canon.group(1).strip() if m_canon else ""
        h1s = [re_space.sub(" ", re_tags.sub("", x)).strip() for x in re_h1.findall(html)]
        m_lang = re_lang.search(html)
        lang = m_lang.group(1).strip().lower() if m_lang else ""

        body = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
        body = re.sub(r"<style[\s\S]*?</style>", " ", body, flags=re.I)
        text = re_space.sub(" ", re_tags.sub(" ", body)).strip().lower()
        words = [w for w in re.split(r"[^\wа-яё-]+", text, flags=re.I) if w]

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
        if not lang.startswith("ru"):
            stats["missing_lang_ru"] += 1
        if not re_og_title.search(html):
            stats["missing_og_title"] += 1
        if not re_og_desc.search(html):
            stats["missing_og_description"] += 1
        if not re_jsonld.search(html):
            stats["missing_jsonld"] += 1
        if re_noindex.search(html):
            stats["has_noindex"] += 1
        if len(words) < 250:
            stats["thin_content_lt_250_words"] += 1
        if title and not (30 <= len(title) <= 75):
            stats["bad_title_len"] += 1
        if desc and not (90 <= len(desc) <= 180):
            stats["bad_description_len"] += 1
        if canon and not canon.startswith("https://x-gu.ru/"):
            stats["bad_canonical_domain"] += 1

    duplicates = {
        "title_duplicate_pages": sum(v for v in title_counter.values() if v > 1),
        "title_duplicate_groups": sum(1 for v in title_counter.values() if v > 1),
        "h1_duplicate_pages": sum(v for v in h1_counter.values() if v > 1),
        "h1_duplicate_groups": sum(1 for v in h1_counter.values() if v > 1),
    }
    return {"stats": stats, "duplicates": duplicates}


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
        "has_noindex": _as_int("SEOHC_MAX_NOINDEX", 0),
        "bad_title_len": _as_int("SEOHC_MAX_BAD_TITLE_LEN", 0),
        "bad_description_len": _as_int("SEOHC_MAX_BAD_DESC_LEN", 0),
        "title_duplicate_pages": _as_int("SEOHC_MAX_TITLE_DUP_PAGES", 0),
        "h1_duplicate_pages": _as_int("SEOHC_MAX_H1_DUP_PAGES", 0),
    }
    breaches: list[str] = []
    for k in ("missing_title", "missing_description", "missing_canonical", "missing_h1", "missing_og_title",
              "missing_og_description", "missing_jsonld", "has_noindex", "bad_title_len", "bad_description_len"):
        if s[k] > limits[k]:
            breaches.append(f"{k}={s[k]} > {limits[k]}")
    if d["title_duplicate_pages"] > limits["title_duplicate_pages"]:
        breaches.append(f"title_duplicate_pages={d['title_duplicate_pages']} > {limits['title_duplicate_pages']}")
    if d["h1_duplicate_pages"] > limits["h1_duplicate_pages"]:
        breaches.append(f"h1_duplicate_pages={d['h1_duplicate_pages']} > {limits['h1_duplicate_pages']}")
    return len(breaches) == 0, breaches


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
        f"Pages: {s['pages_total']}\n"
        f"missing_title={s['missing_title']}, missing_description={s['missing_description']}, missing_canonical={s['missing_canonical']}, missing_h1={s['missing_h1']}\n"
        f"missing_og_title={s['missing_og_title']}, missing_og_description={s['missing_og_description']}, missing_jsonld={s['missing_jsonld']}\n"
        f"bad_title_len={s['bad_title_len']}, bad_description_len={s['bad_description_len']}, noindex={s['has_noindex']}\n"
        f"title_dup_pages={d['title_duplicate_pages']}, h1_dup_pages={d['h1_duplicate_pages']}"
    )
    if breaches:
        text += "\\nBreaches: " + "; ".join(breaches[:10])

    print(text)
    notify_enabled = _as_bool("SEOHC_NOTIFY_ENABLED", True)
    notify_on_ok = _as_bool("SEOHC_NOTIFY_ON_OK", True)
    if notify_enabled and (notify_on_ok or not ok):
        send_telegram(text)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
