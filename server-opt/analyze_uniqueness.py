#!/usr/bin/env python3
"""Sample-based uniqueness & SEO audit (gentle on a 1-CPU box — no full scan).

Samples:
  A) same keyword across many cities  -> measures geo-only differentiation
  B) same city across many keywords    -> measures keyword differentiation
  C) random pages                      -> overall title/desc/H1 dup + thin content

For body similarity we strip tags/scripts/styles, then NORMALIZE by removing the
city name, so two pages that differ ONLY by city collapse to the same text ->
exposes template duplication (doorway/thin-content risk).
"""
from __future__ import annotations

import os
import re
import random
import hashlib
from collections import Counter
from pathlib import Path

ROOT = Path("/var/www/x-gu.ru/current")
random.seed(2026)

re_title = re.compile(r"<title>(.*?)</title>", re.I | re.S)
re_desc = re.compile(r'name=["\']description["\'][^>]*content=["\'](.*?)["\']', re.I | re.S)
re_h1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
re_script = re.compile(r"<script[\s\S]*?</script>", re.I)
re_style = re.compile(r"<style[\s\S]*?</style>", re.I)
re_tags = re.compile(r"<[^>]+>")
re_space = re.compile(r"\s+")


def visible_text(html: str) -> str:
    b = re_script.sub(" ", html)
    b = re_style.sub(" ", b)
    b = re_tags.sub(" ", b)
    return re_space.sub(" ", b).strip()


def words(text: str) -> int:
    return len([w for w in re.split(r"[^\wа-яёА-ЯЁ-]+", text) if w])


def norm_body(text: str, city: str) -> str:
    """Remove city tokens so geo-only-different pages collapse to identical text."""
    t = text.lower()
    for token in {city.lower(), city.lower()[:-1]}:  # crude stem for case endings
        if len(token) > 3:
            t = t.replace(token, "<city>")
    # drop digits (seeded stats vary) to isolate structural template text
    t = re.sub(r"\d+", "#", t)
    return t


def grab(path: Path):
    html = path.read_text(encoding="utf-8", errors="ignore")
    t = re_title.search(html)
    d = re_desc.search(html)
    h = re_h1.search(html)
    title = re_space.sub(" ", re_tags.sub("", t.group(1))).strip() if t else ""
    desc = re_space.sub(" ", re_tags.sub("", d.group(1))).strip() if d else ""
    h1 = re_space.sub(" ", re_tags.sub("", h.group(1))).strip() if h else ""
    body = visible_text(html)
    return {"title": title, "desc": desc, "h1": h1, "body": body, "wc": words(body)}


def list_cities():
    return [d for d in os.listdir(ROOT)
            if (ROOT / d).is_dir() and d not in ("sitemaps",) and not d.startswith(".")]


def report_group(name, pages_meta, city_for_norm=None):
    titles = Counter(p["title"] for p in pages_meta)
    descs = Counter(p["desc"] for p in pages_meta)
    h1s = Counter(p["h1"] for p in pages_meta)
    # normalized body dedup
    if city_for_norm == "per":
        norm = Counter(norm_body(p["body"], p["_city"]) for p in pages_meta)
    else:
        norm = Counter(re.sub(r"\d+", "#", p["body"].lower()) for p in pages_meta)
    wc = [p["wc"] for p in pages_meta]
    n = len(pages_meta)
    print(f"\n== {name} (n={n}) ==")
    print(f"  unique titles:      {len(titles)}/{n}")
    print(f"  unique descriptions:{len(descs)}/{n}")
    print(f"  unique H1:          {len(h1s)}/{n}")
    print(f"  unique NORMALIZED bodies (city+digits removed): {len(norm)}/{n}")
    print(f"  word count: min={min(wc)} med={sorted(wc)[n//2]} max={max(wc)} | thin(<250)={sum(1 for w in wc if w<250)}")
    # show a duplicate normalized body cluster size
    top = norm.most_common(1)[0]
    print(f"  largest identical-template cluster: {top[1]} pages share the same normalized body")


def main():
    cities = list_cities()
    # A) same keyword across cities
    kw = "seo-audit-saita"
    sample_cities = random.sample(cities, min(15, len(cities)))
    A = []
    for c in sample_cities:
        p = ROOT / c / kw / "index.html"
        if p.is_file():
            m = grab(p); m["_city"] = c; A.append(m)
    if A:
        report_group(f"SAME KEYWORD '{kw}' across {len(A)} cities", A, city_for_norm="per")

    # B) same city across keywords
    city = "moskva" if (ROOT / "moskva").is_dir() else cities[0]
    kws = [d for d in os.listdir(ROOT / city) if (ROOT / city / d).is_dir()]
    sample_kws = random.sample(kws, min(15, len(kws)))
    B = []
    for k in sample_kws:
        p = ROOT / city / k / "index.html"
        if p.is_file():
            m = grab(p); m["_city"] = city; B.append(m)
    if B:
        report_group(f"SAME CITY '{city}' across {len(B)} keywords", B, city_for_norm="per")

    # C) random pages
    pool = []
    for c in random.sample(cities, min(40, len(cities))):
        subs = [d for d in os.listdir(ROOT / c) if (ROOT / c / d).is_dir()]
        if subs:
            k = random.choice(subs)
            pool.append((c, ROOT / c / k / "index.html"))
    C = []
    for c, p in pool:
        if p.is_file():
            m = grab(p); m["_city"] = c; C.append(m)
    if C:
        report_group(f"RANDOM {len(C)} pages (different city+keyword)", C, city_for_norm="per")


if __name__ == "__main__":
    main()
