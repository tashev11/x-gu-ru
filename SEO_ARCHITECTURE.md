# X-GU.RU — как работают тысячи SEO-страниц

Этот документ описывает programmatic SEO-модель x-gu.ru: какие URL физически существуют, какие должны индексироваться, как принимается решение `index/noindex` и как находить каннибализацию между похожими страницами.

## 1. Исторический масштаб

В истории проекта зафиксирована исходная точка перед первым shrink:

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

Это безопаснее 37 тысяч индексируемых URL, но всё ещё слишком грубо для долгосрочного SEO.

## 2. Физическая страница != индексируемая страница

Generator может хранить десятки тысяч физических каталогов:

```text
/<city>/<service>/index.html
```

Но release policy решает судьбу каждого URL в поиске.

Закрытая страница:

```text
файл существует
→ meta robots = noindex
→ URL отсутствует в sitemap
→ открытые хабы на него не ссылаются
→ после проверки может быть удалён физически
```

Открытая страница:

```text
index,follow
→ self-canonical
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

Все комбинации `open_cities × open_services` считаются открытыми.

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

Получается:

```text
moskva/                       OPEN hub
moskva/prodvizhenie-saita     OPEN
moskva/seo-audit-saita        OPEN

tver/                         OPEN hub
tver/prodvizhenie-saita       OPEN
tver/seo-audit-saita          CLOSED
```

`server-opt/index_policy.example.json` уже использует v2. Исторический baseline остаётся v1 только для совместимости и аварийного восстановления.

## 4. Один источник истины для index/noindex

Одинаковая policy-семантика используется в:

- `content_generator.py` — robots при рендере;
- `server-opt/shrink_index.py` — sitemap и массовый index/noindex;
- `seo_healthcheck.py` — ожидаемая индексируемость каждого URL;
- `server-opt/rerender_open_hubs.py` — ссылки городского хаба;
- `server-opt/rerender_hubs_home.py` — homepage и city hubs;
- `seo_rebuild_broken.py` — ремонт страниц без возврата глобальной service-матрицы;
- `server-opt/predeploy_check.py` — проверка policy digest и фактического состояния release.

Это защищает от ситуации, когда sitemap, robots и перелинковка принимают разные решения для одного URL.

## 5. Release contract

Каждый release содержит:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

`.xgu-index-keep.json` хранит нормализованную policy. Для v2 сохраняются `policy_version=2`, `policy_mode=pairs`, `open_cities`, `open_pairs` и производный `open_services` только для инвентаризации.

`.xgu-whitelist.txt` — защищённые URL-исключения.

`.xgu-release.json` — Git SHA и fingerprint финального содержимого release.

## 6. На основании чего открывать пару `город/услуга`

Одной возможности сгенерировать страницу недостаточно. Решение должно опираться на три группы сигналов.

### Search evidence

- URL находится в поиске Яндекса;
- Google Search Console показывает impressions/clicks;
- есть реальный поисковый спрос;
- URL вручную помечен как бизнес-критичный;
- при наличии данных учитываются лиды/конверсии/внешние ссылки.

### Page quality

- корректные Title/H1/Description;
- достаточный полезный видимый контент;
- self-canonical;
- валидный JSON-LD;
- нет точного дубля другой страницы;
- near-duplicate отмечается для ручного review;
- нет фиктивных отзывов, рейтингов и KPI.

### Internal graph

- индексируемая страница имеет входящую ссылку;
- открытые страницы не раздают массово ссылки на закрытые URL;
- city hub ведёт только на реально открытые для этого города service pages;
- sitemap содержит только канонические индексируемые URL.

## 7. Полный аудит тысяч страниц

`server-opt/programmatic_seo_audit.py` проходит по всему release:

```bash
python server-opt/programmatic_seo_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-programmatic-seo.json
```

Он показывает:

- physical pages;
- indexable pages;
- policy-closed pages;
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

Скрипт не меняет production whitelist автоматически. GSC собирается с `startRow` pagination.

## 9. Quality audit только для страниц с поисковым сигналом

Перед рекомендацией пары в policy v2 запускается:

```bash
python server-opt/pair_quality_audit.py \
  --root /var/www/x-gu.ru/current \
  --evidence /opt/p3-app/data/search_evidence.json \
  --out /opt/p3-app/data/pair_quality.json
```

Он проверяет даже страницы, которые сейчас `noindex`, если у них уже есть Yandex/GSC evidence.

Жёсткие дефекты вроде отсутствующей страницы, thin content, canonical mismatch, invalid JSON-LD или exact duplicate переводят URL в состояние `improve_before_index`.

Near-duplicate сохраняется как `review_similarity`: это повод проверить интент вручную, а не автоматически закрыть URL.

## 10. Автоматическая подготовка policy v2, но не автопубликация

`server-opt/build_pair_policy.py` пересекает **search evidence + page quality** и готовит:

```text
/opt/p3-app/data/index_policy.v2.candidate.json
/opt/p3-app/data/index_policy.v2.review.json
```

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

Даже с `--apply` он пишет только review-файлы. Candidate всегда содержит:

```json
"example_only": true
```

Поэтому `shrink_index.py` не сможет применить его как production policy без ручного review.

## 11. Каннибализация: когда несколько страниц борются за один запрос

Для массового city/service SEO недостаточно следить только за дублями. Две разные страницы могут быть формально уникальными, но Google может показывать их по одним и тем же запросам.

Используется:

```bash
python server-opt/gsc_cannibalization_report.py \
  --days 90 \
  --out /opt/p3-app/data/gsc_cannibalization.json
```

Инструмент запрашивает GSC с dimensions:

```text
query + page
```

и показывает:

- запросы, по которым получают показы несколько URL;
- количество затронутых страниц;
- пары конкурирующих страниц;
- отдельный список **same-city competing pairs** — самый важный класс для programmatic SEO;
- shared queries и shared impressions.

Отсутствие пары в отчёте не является доказательством отсутствия каннибализации: Search Console Search Analytics возвращает ограниченный верхний слой данных.

## 12. Review-план консолидации

`server-opt/build_cannibalization_review.py` объединяет отчёт каннибализации с `pair_quality.json`:

```bash
python server-opt/build_cannibalization_review.py
python server-opt/build_cannibalization_review.py --apply
```

Он формирует только:

```text
/opt/p3-app/data/cannibalization.review.json
```

Для каждой same-city пары он показывает:

- реальные клики/показы/среднюю позицию по общим запросам;
- quality status обеих страниц;
- рекомендуемый основной URL;
- альтернативный URL для ручного сравнения интента;
- уровень уверенности рекомендации.

**Никакие redirect/canonical/noindex изменения автоматически не применяются.** Совпадение запросов ещё не означает одинаковый интент. Перед объединением надо проверить контент, конверсии, ссылки и бизнес-задачу обеих страниц.

## 13. Целевой SEO-конвейер

```text
десятки тысяч физических страниц
        ↓
Yandex + GSC + manual evidence
        ↓
build_search_evidence.py
        ↓
pair_quality_audit.py
        ↓
build_pair_policy.py
        ↓
review-only exact pair candidate
        ↓
gsc_cannibalization_report.py
        ↓
build_cannibalization_review.py
        ↓
ручной review интентов / конкурирующих URL
        ↓
reviewed production policy v2
        ↓
shrink release candidate
        ↓
city-specific internal linking
        ↓
full programmatic SEO audit
        ↓
strict predeploy
        ↓
production
```

## 14. Что считать успехом

Не количество созданных страниц.

Главные показатели:

- сколько физических URL существует;
- сколько URL разрешено policy;
- сколько реально indexed/searchable;
- сколько имеют impressions и clicks;
- median position;
- доля открытых URL с 0 impressions за 28/90 дней;
- число thin/orphan/exact/near-duplicate страниц;
- число `open → closed` ссылок;
- число запросов с несколькими конкурирующими страницами;
- число same-city competing pairs;
- leads/conversions по landing pages;
- страницы, исключённые поисковиком как low-value/duplicate/crawled-not-indexed.

## 15. Практический принцип x-gu.ru

**Генерировать можно десятки тысяч страниц. Индексировать нужно только конкретные URL с отдельным поисковым интентом, достаточным качеством и доказательствами ценности. Если несколько URL делят один интент — сначала измерить каннибализацию, затем вручную решить, разводить интенты или консолидировать страницы.**

Policy v2 уже реализована в tooling. На production она должна включаться только после снятия текущих поисковых данных и ревью точных `open_pairs`.
