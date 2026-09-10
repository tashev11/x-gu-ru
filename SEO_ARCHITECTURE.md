# X-GU.RU — как работают тысячи SEO-страниц

Этот документ описывает programmatic SEO-модель x-gu.ru: какие URL физически существуют, какие должны индексироваться и почему число сгенерированных файлов нельзя путать с числом страниц, которые стоит отдавать поиску.

## 1. Исторический масштаб

В сохранённой истории проекта зафиксирована исходная точка перед первым shrink:

- sitemap: **37 625 URL**;
- после первого shrink: **1 097 URL**;
- Яндекс на тот момент показывал в поиске только **41 URL**.

Исторический baseline `server-opt/index_policy.baseline.json` содержит 57 открытых городов и 18 открытых услуг. Policy v1 открывает их декартово произведение:

```text
57 × 18 = 1 026 city/service landing pages
+ 57 city hubs
+ homepage
+ /privacy/
+ search-protected whitelist exceptions
```

Это было существенно безопаснее 37 тысяч URL, но всё ещё слишком грубо для долгосрочного SEO.

## 2. Физическая страница != индексируемая страница

Generator может хранить десятки тысяч физических каталогов:

```text
/<city>/<service>/index.html
```

Но release policy решает судьбу каждой страницы в поиске.

Закрытая страница:

```text
файл существует
→ meta robots = noindex
→ URL отсутствует в sitemap
→ открытые хабы на него не ссылаются
→ при необходимости после проверки может быть удалён
```

Открытая страница:

```text
index,follow
→ canonical на саму себя
→ присутствует в sitemap
→ получает входящие crawlable ссылки
→ проходит quality/uniqueness checks
```

Поэтому 30–40 тысяч файлов на диске не должны означать 30–40 тысяч URL в индексе.

## 3. Две версии index policy

Общая реализация находится в `index_policy.py`.

### Policy v1 — историческая матрица

```json
{
  "policy_version": 1,
  "open_cities": ["moskva", "tver"],
  "open_services": ["prodvizhenie-saita", "seo-audit-saita"]
}
```

Она означает:

```text
moskva/prodvizhenie-saita     OPEN
moskva/seo-audit-saita        OPEN
tver/prodvizhenie-saita       OPEN
tver/seo-audit-saita          OPEN
```

То есть все комбинации `open_cities × open_services`.

### Policy v2 — точные пары

```json
{
  "policy_version": 2,
  "open_cities": ["moskva", "tver"],
  "open_pairs": [
    "moskva/prodvizhenie-saita",
    "moskva/seo-audit-saita",
    "tver/prodvizhenie-saita"
  ]
}
```

Она означает:

```text
moskva/                       OPEN hub
moskva/prodvizhenie-saita     OPEN
moskva/seo-audit-saita        OPEN

tver/                         OPEN hub
tver/prodvizhenie-saita       OPEN
tver/seo-audit-saita          CLOSED
```

Пара не может быть открыта, если её город отсутствует в `open_cities`.

`server-opt/index_policy.example.json` уже использует v2. Исторический baseline остаётся v1 только для совместимости и аварийного восстановления.

## 4. Один источник истины для index/noindex

Одинаковая policy-семантика используется в:

- `content_generator.py` — robots при рендере;
- `server-opt/shrink_index.py` — sitemap и массовый index/noindex;
- `seo_healthcheck.py` — проверка ожидаемой индексируемости;
- `server-opt/rerender_open_hubs.py` — ссылки городского хаба;
- `server-opt/rerender_hubs_home.py` — homepage и хабы;
- `seo_rebuild_broken.py` — ремонт страниц без возврата глобальной service-матрицы.

Это важно: sitemap, robots и перелинковка не должны принимать разные решения для одного URL.

## 5. Release contract

Каждый release содержит:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

`.xgu-index-keep.json` хранит нормализованную policy. Для v2 там сохраняются `policy_version=2`, `policy_mode=pairs`, `open_cities`, `open_pairs` и производный список `open_services` только для инвентаризации.

`.xgu-whitelist.txt` — отдельные защищённые URL, которые нельзя случайно закрыть общей policy.

`.xgu-release.json` — Git SHA и fingerprint финального содержимого release.

## 6. Откуда брать решение, какие пары открывать

Индексировать страницу `/<city>/<service>/` только потому, что она может быть сгенерирована, нельзя.

Нужны три группы сигналов.

### Search evidence

- URL уже находится в поиске Яндекса;
- Google Search Console показывает impressions/clicks;
- есть реальный поисковый спрос;
- есть исторический трафик/лиды/внешние ссылки;
- URL вручную помечен как бизнес-критичный.

### Page quality

- уникальные Title/H1/Description;
- достаточный полезный видимый контент;
- нет почти полного дубля другой страницы;
- контент отвечает именно интенту `услуга + город`;
- нет фиктивных отзывов, рейтингов или KPI;
- корректная structured data.

### Internal graph

- индексируемая страница имеет входящую ссылку;
- открытые страницы не раздают массово ссылки на закрытые URL;
- city hub ведёт только на реально открытые для этого города service pages;
- sitemap содержит только канонические индексируемые URL.

## 7. Полный аудит тысяч страниц

`server-opt/programmatic_seo_audit.py` проходит по всему release, но quality-метрики считает для реально индексируемого корпуса.

```bash
python server-opt/programmatic_seo_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-programmatic-seo.json
```

Он показывает:

- physical pages;
- indexable pages;
- policy-closed pages;
- типы открытых страниц;
- thin indexable pages;
- orphan indexable pages;
- ссылки `open → closed`;
- exact duplicate bodies;
- near-duplicate bodies по SimHash.

Near-duplicate — сигнал для ревью, а не автоматический приговор странице.

## 8. Сначала поисковые данные, потом shrink

`server-opt/build_search_evidence.py` объединяет:

```text
Яндекс Вебмастер
+
Google Search Console
+
manual protected URLs
```

и формирует:

```text
/opt/p3-app/data/search_evidence.json
/opt/p3-app/data/whitelist.candidate.txt
```

Скрипт не меняет production whitelist автоматически.

GSC собирается с `startRow` pagination, поэтому решение не ограничивается первой порцией строк.

## 9. Автоматическая подготовка v2, но не автопубликация

`server-opt/build_pair_policy.py` читает `search_evidence.json` и готовит:

```text
/opt/p3-app/data/index_policy.v2.candidate.json
/opt/p3-app/data/index_policy.v2.review.json
```

Пример:

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

Даже с `--apply` он пишет **только review-файлы**. Candidate всегда содержит:

```json
"example_only": true
```

Поэтому `shrink_index.py` технически откажется применять его как production policy.

Чтобы продвинуть candidate в production, нужно вручную:

1. проверить конкретные `open_pairs`;
2. проверить full-corpus quality audit;
3. удалить слабые пары;
4. при необходимости добавить бизнес-критичные пары;
5. выставить `example_only=false`;
6. записать `reviewed_at`;
7. обновить `source_note` с источником решения;
8. только затем использовать файл как `/opt/p3-app/data/index_policy.json`.

## 10. Целевой SEO-конвейер

```text
десятки тысяч физических страниц
        ↓
Yandex + GSC + manual evidence
        ↓
build_search_evidence.py
        ↓
build_pair_policy.py
        ↓
review-only policy v2 candidate
        ↓
full programmatic SEO audit
        ↓
ручной review exact city/service pairs
        ↓
reviewed production policy v2
        ↓
shrink release candidate
        ↓
city-specific internal linking
        ↓
strict predeploy
        ↓
production
```

## 11. Что считать успехом

Не количество созданных страниц.

Главные показатели:

- сколько физических URL существует;
- сколько URL разрешено policy;
- сколько реально indexed/searchable;
- сколько имеют impressions;
- сколько имеют clicks;
- median position;
- доля открытых URL с 0 impressions за 28/90 дней;
- число thin/orphan/near-duplicate страниц;
- число `open → closed` ссылок;
- leads/conversions по landing pages;
- страницы, которые поисковик исключает как low-value/duplicate/crawled-not-indexed.

## 12. Практический принцип x-gu.ru

**Генерировать можно десятки тысяч страниц. Индексировать нужно только те конкретные URL, для которых есть отдельный поисковый интент, достаточное качество и доказательства ценности.**

Policy v2 уже реализована в tooling. На production она должна включаться только после снятия текущих поисковых данных и ревью точных `open_pairs`.
