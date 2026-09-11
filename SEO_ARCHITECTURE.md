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

В v2 `tver/seo-audit-saita` остаётся закрытой, даже если город Tver и услуга SEO-аудита встречаются в других открытых URL.

## 4. Один источник истины

Одинаковая policy-семантика используется в генераторе, `shrink_index.py`, `seo_healthcheck.py`, hub-rerender, rebuild и strict predeploy. Sitemap, robots и перелинковка не должны принимать разные решения для одного URL.

## 5. Release contract

Каждый release содержит:

```text
.xgu-index-keep.json
.xgu-whitelist.txt
.xgu-release.json
```

Policy, whitelist и fingerprint переключаются вместе с HTML одним release-switch.

## 6. На основании чего открывать `город/услуга`

**Search evidence:** Яндекс, GSC impressions/clicks, ручная бизнес-защита, при наличии — лиды/конверсии/внешние ссылки.

**Page quality:** Title/H1/Description, достаточный контент, self-canonical, валидный JSON-LD, отсутствие exact duplicate, ручной review near-duplicate.

**Internal graph:** входящие ссылки, отсутствие массовых `open → closed` ссылок, city-specific hub links и корректный sitemap.

## 7. Полный аудит корпуса

```bash
python server-opt/programmatic_seo_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-programmatic-seo.json
```

`server-opt/programmatic_seo_audit.py` показывает physical/indexable/closed pages, thin pages, orphan pages, `open → closed` links, exact duplicates и near-duplicates.

## 8. Глубина обхода и шаблонность одной услуги

```bash
python server-opt/link_graph_cluster_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-link-cluster.json
```

`server-opt/link_graph_cluster_audit.py` показывает:

- сколько индексируемых URL достижимы от главной;
- URL в замкнутых кластерах, недостижимых из `/`;
- максимальную crawl depth;
- распределение страниц по глубине;
- страницы глубже заданного порога;
- exact/near-duplicate кластеры одной услуги по разным городам.

Это важно, потому что 50 URL могут иметь разные города и формально разные мета-теги, но оставаться практически одним шаблонным документом.

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

Жёсткие дефекты переводят страницу в `improve_before_index`. Near-duplicate получает `review_similarity`, а не автоматический запрет.

## 11. Review-only policy v2

`server-opt/build_pair_policy.py` пересекает search evidence и page quality:

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

Candidate всегда содержит `"example_only": true`, поэтому не может быть случайно применён как production policy.

## 12. Каннибализация

```bash
python server-opt/gsc_cannibalization_report.py \
  --days 90 \
  --out /opt/p3-app/data/gsc_cannibalization.json
```

`server-opt/gsc_cannibalization_report.py` запрашивает GSC в разрезе `query + page` и показывает affected pages, competing page pairs и отдельные **same-city competing pairs**.

## 13. Review-план консолидации

```bash
python server-opt/build_cannibalization_review.py
python server-opt/build_cannibalization_review.py --apply
```

`server-opt/build_cannibalization_review.py` объединяет каннибализацию с `pair_quality.json` и предлагает основной URL на основе реальных кликов/показов/позиции и качества страницы.

**Redirect/canonical/noindex автоматически не меняются.** Общие запросы ещё не доказывают одинаковый интент.

## 14. Жизненный цикл whitelist

Whitelist — это защита от случайного закрытия, а не пожизненная индексационная привилегия.

```bash
python server-opt/whitelist_lifecycle_report.py \
  --whitelist /var/www/x-gu.ru/current/.xgu-whitelist.txt \
  --evidence /opt/p3-app/data/search_evidence.json \
  --json-out /tmp/xgu-whitelist-lifecycle.json
```

`server-opt/whitelist_lifecycle_report.py` разделяет защищённые URL на:

- имеющие актуальный Yandex/GSC/manual signal;
- stale review candidates — защита есть, текущего сигнала нет;
- URL, отсутствующие в текущем evidence dataset;
- URL с новым поисковым сигналом, которые ещё не входят в whitelist.

**Автоматического удаления нет.** Перед снятием защиты нужно проверить backlinks, конверсии, бизнес-критичность и исторические данные.

## 15. Целевой SEO-конвейер

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
whitelist_lifecycle_report.py
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

## 16. KPI

Смотрим не на количество созданных страниц, а на:

- physical / policy-open / indexed URLs;
- impressions, clicks и median position;
- долю open URL с 0 impressions за 28/90 дней;
- thin/orphan/unreachable/deep pages;
- exact/near-duplicate pages и same-service cross-city clusters;
- `open → closed` links;
- queries with multiple competing pages;
- same-city competing pairs;
- stale whitelist review candidates;
- leads/conversions по landing pages;
- low-value/duplicate/crawled-not-indexed exclusions.

## 17. Практический принцип x-gu.ru

**Генерировать можно десятки тысяч страниц. Индексировать нужно только конкретные URL с отдельным поисковым интентом, достаточным качеством, нормальной внутренней доступностью и доказательствами ценности. Если несколько URL делят один интент — сначала измерить каннибализацию, затем вручную решить, разводить интенты или консолидировать страницы.**
