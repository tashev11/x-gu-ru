# X-GU.RU — как работают тысячи SEO-страниц

Этот документ описывает programmatic SEO-модель x-gu.ru: какие URL физически существуют, какие должны индексироваться и как мы принимаем решение `index/noindex` для десятков тысяч сгенерированных страниц.

## 1. Исторический масштаб

В истории проекта зафиксирована исходная точка перед первым shrink:

- sitemap: **37 625 URL**;
- после первого shrink: **1 097 URL**;
- Яндекс на тот момент показывал в поиске только **41 URL**.

Исторический baseline `server-opt/index_policy.baseline.json` содержит 57 открытых городов и 18 открытых услуг. Policy v1 открывает их декартово произведение: `57 × 18 = 1 026` service landing pages плюс city hubs, homepage, privacy и whitelist-исключения.

Это безопаснее 37 тысяч индексируемых URL, но всё ещё слишком грубо для долгосрочного SEO.

## 2. Физическая страница != индексируемая страница

Generator может хранить десятки тысяч физических каталогов `/<city>/<service>/index.html`.

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

## 3. Policy v1 и v2

Общая реализация находится в `index_policy.py`.

Policy v1 — историческая матрица:

```json
{
  "policy_version": 1,
  "open_cities": ["moskva", "tver"],
  "open_services": ["prodvizhenie-saita", "seo-audit-saita"]
}
```

Policy v2 — точные пары:

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

В v2 `tver/seo-audit-saita` остаётся закрытой, даже если и город Tver, и услуга SEO-аудита встречаются в других открытых URL.

`server-opt/index_policy.example.json` использует v2. Исторический baseline остаётся v1 только для совместимости и аварийного восстановления.

## 4. Один источник истины

Одинаковая policy-семантика используется в:

- `content_generator.py`;
- `server-opt/shrink_index.py`;
- `seo_healthcheck.py`;
- `server-opt/rerender_open_hubs.py`;
- `server-opt/rerender_hubs_home.py`;
- `seo_rebuild_broken.py`;
- `server-opt/predeploy_check.py`.

Sitemap, robots и перелинковка не должны принимать разные решения для одного URL.

## 5. Release contract

Каждый release содержит:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

Policy, whitelist и fingerprint переключаются вместе с HTML одним release-switch.

## 6. На основании чего открывать `город/услуга`

Нужны три группы сигналов.

**Search evidence:** Яндекс, GSC impressions/clicks, ручная бизнес-защита, при наличии — лиды/конверсии/внешние ссылки.

**Page quality:** Title/H1/Description, достаточный контент, self-canonical, валидный JSON-LD, отсутствие exact duplicate, ручной review near-duplicate.

**Internal graph:** входящие ссылки, отсутствие массовых `open → closed` ссылок, city-specific hub links и корректный sitemap.

## 7. Полный аудит корпуса

`server-opt/programmatic_seo_audit.py` проходит по всему release:

```bash
python server-opt/programmatic_seo_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-programmatic-seo.json
```

Он показывает physical/indexable/closed pages, thin pages, orphan pages, `open → closed` links, exact duplicates и near-duplicates по SimHash.

## 8. Глубина обхода и шаблонность одной услуги по городам

`server-opt/link_graph_cluster_audit.py` отвечает на два вопроса, которых недостаточно для обычного orphan-check:

```bash
python server-opt/link_graph_cluster_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-link-cluster.json
```

Он показывает:

- сколько индексируемых URL реально достижимы от главной;
- сколько URL находятся в замкнутом внутреннем кластере и не достижимы из `/`;
- максимальную crawl depth;
- распределение страниц по глубине 0/1/2/3/...;
- страницы глубже заданного порога;
- exact/near-duplicate кластеры **одной и той же услуги по разным городам**.

Последняя метрика особенно важна: 50 URL могут иметь разные города и формально разные Title, но оставаться практически одним шаблонным документом.

## 9. Сначала поисковые данные, потом shrink

`server-opt/build_search_evidence.py` объединяет Яндекс Вебмастер, Google Search Console и manual protected URLs.

Результат:

```text
/opt/p3-app/data/search_evidence.json
/opt/p3-app/data/whitelist.candidate.txt
```

Скрипт не меняет production whitelist автоматически. GSC собирается с `startRow` pagination.

## 10. Quality audit страниц с поисковым сигналом

```bash
python server-opt/pair_quality_audit.py \
  --root /var/www/x-gu.ru/current \
  --evidence /opt/p3-app/data/search_evidence.json \
  --out /opt/p3-app/data/pair_quality.json
```

Проверяются даже текущие `noindex` URL, если у них уже есть поисковый сигнал.

Жёсткие дефекты переводят страницу в `improve_before_index`. Near-duplicate получает `review_similarity`, а не автоматический запрет.

## 11. Review-only policy v2

`server-opt/build_pair_policy.py` пересекает search evidence и page quality:

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

Результат:

```text
index_policy.v2.candidate.json
index_policy.v2.review.json
```

Candidate всегда содержит `"example_only": true`, поэтому не может быть случайно применён как production policy.

## 12. Каннибализация

`server-opt/gsc_cannibalization_report.py` запрашивает GSC в разрезе `query + page`:

```bash
python server-opt/gsc_cannibalization_report.py \
  --days 90 \
  --out /opt/p3-app/data/gsc_cannibalization.json
```

Он показывает запросы с несколькими URL, affected pages, competing page pairs и отдельные **same-city competing pairs**.

Это диагностический сигнал: Search Console Search Analytics может возвращать не весь хвост строк.

## 13. Review-план консолидации

`server-opt/build_cannibalization_review.py` объединяет GSC cannibalization и `pair_quality.json`:

```bash
python server-opt/build_cannibalization_review.py
python server-opt/build_cannibalization_review.py --apply
```

Результат — только `/opt/p3-app/data/cannibalization.review.json`.

Для каждой same-city пары он показывает реальные клики/показы/среднюю позицию по общим запросам, quality status, рекомендуемый основной URL и альтернативы для ручного сравнения интента.

**Redirect/canonical/noindex автоматически не меняются.** Общие запросы ещё не доказывают одинаковый интент.

## 14. Целевой SEO-конвейер

```text
десятки тысяч физических страниц
        ↓
programmatic_seo_audit.py
        ↓
link_graph_cluster_audit.py
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
strict predeploy
        ↓
production
```

## 15. KPI

Смотрим не на количество созданных страниц, а на:

- physical / policy-open / indexed URLs;
- impressions, clicks и median position;
- долю open URL с 0 impressions за 28/90 дней;
- thin/orphan/unreachable/deep pages;
- exact/near-duplicate pages и same-service cross-city clusters;
- `open → closed` links;
- queries with multiple competing pages;
- same-city competing pairs;
- leads/conversions по landing pages;
- low-value/duplicate/crawled-not-indexed exclusions.

## 16. Практический принцип x-gu.ru

**Генерировать можно десятки тысяч страниц. Индексировать нужно только конкретные URL с отдельным поисковым интентом, достаточным качеством, нормальной внутренней доступностью и доказательствами ценности. Если несколько URL делят один интент — сначала измерить каннибализацию, затем вручную решить, разводить интенты или консолидировать страницы.**
