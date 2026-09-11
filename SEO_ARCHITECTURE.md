# X-GU.RU — как работают тысячи SEO-страниц

Этот документ описывает programmatic SEO-модель x-gu.ru: какие URL физически существуют, какие должны индексироваться и как принимается решение `index/noindex` для десятков тысяч сгенерированных страниц.

## 1. Исторический масштаб

В истории проекта зафиксирована исходная точка перед первым shrink:

- sitemap: **37 625 URL**;
- после первого shrink: **1 097 URL**;
- Яндекс на тот момент показывал в поиске только **41 URL**.

Исторический baseline содержит 57 открытых городов и 18 услуг. Policy v1 открывает их декартово произведение: `57 × 18 = 1 026` service landing pages плюс city hubs, homepage, privacy и whitelist-исключения.

## 2. Физическая страница != индексируемая страница

Generator может хранить десятки тысяч физических `/<city>/<service>/index.html`.

Закрытая страница: файл существует → `noindex` → отсутствует в sitemap → открытые хабы на неё не ссылаются.

Открытая страница: `index,follow` → self-canonical → sitemap → внутренняя ссылка → quality checks.

## 3. Policy v1 и v2

Общая реализация находится в `index_policy.py`.

Policy v1 — матрица `open_cities × open_services`.

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

В v2 `tver/seo-audit-saita` остаётся закрытой, даже если город и услуга встречаются в других открытых URL.

## 4. Один источник истины

Одинаковая policy-семантика используется в генераторе, `shrink_index.py`, `seo_healthcheck.py`, hub-rerender, rebuild и strict predeploy. Sitemap, robots и перелинковка не должны принимать разные решения для одного URL.

## 5. Release contract

Каждый release содержит `.xgu-index-keep.json`, `.xgu-whitelist.txt` и `.xgu-release.json`. Policy, whitelist и fingerprint переключаются вместе с HTML одним release-switch.

## 6. На основании чего открывать `город/услуга`

**Search evidence:** Яндекс, GSC impressions/clicks, manual protection, при наличии — лиды/конверсии/внешние ссылки.

**Page quality:** Title/H1/Description, достаточный контент, self-canonical, валидный JSON-LD, отсутствие exact duplicate, ручной review near-duplicate.

**Internal graph:** входящие ссылки, crawl depth, отсутствие массовых `open → closed` links, city-specific hub links и корректный sitemap.

## 7. Полный аудит корпуса

```bash
python server-opt/programmatic_seo_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-programmatic-seo.json
```

Показывает physical/indexable/closed pages, thin pages, orphan pages, `open → closed` links, exact и near duplicates.

## 8. Глубина обхода и шаблонность одной услуги

```bash
python server-opt/link_graph_cluster_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-link-cluster.json
```

`server-opt/link_graph_cluster_audit.py` показывает достижимость от главной, crawl depth и exact/near-duplicate кластеры одной услуги по разным городам.

## 9. Шаблонность Title/H1/Description и пересечение интентов

Исторический generator строит Title по формуле вида `{service} в {city} | СЕО ГУРУ`, а Description использует общую региональную формулу. Это не автоматическая SEO-ошибка, но сильный шаблонный отпечаток.

```bash
python server-opt/metadata_intent_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-metadata-intent.json
```

`server-opt/metadata_intent_audit.py` измеряет похожесть Title/H1/Description одной услуги по городам, service-wide template risk и cross-service intent overlap внутри одного города.

Высокая похожесть — сигнал для ревью. Не надо рандомить Title только ради формальной «уникальности».

## 10. Сначала поисковые данные, потом shrink

`server-opt/build_search_evidence.py` объединяет Яндекс Вебмастер, Google Search Console и manual protected URLs.

Результат:

```text
/opt/p3-app/data/search_evidence.json
/opt/p3-app/data/whitelist.candidate.txt
```

Скрипт не меняет production whitelist. GSC собирается с `startRow` pagination.

## 11. Quality audit страниц с поисковым сигналом

```bash
python server-opt/pair_quality_audit.py \
  --root /var/www/x-gu.ru/current \
  --evidence /opt/p3-app/data/search_evidence.json \
  --out /opt/p3-app/data/pair_quality.json
```

Жёсткие дефекты переводят страницу в `improve_before_index`. Near-duplicate получает `review_similarity`.

Quality report хранит `generated_at` и `source_evidence_generated_at`.

## 12. Точное покрытие текущей policy

```bash
python server-opt/index_coverage_review.py \
  --root /var/www/x-gu.ru/current \
  --evidence /opt/p3-app/data/search_evidence.json \
  --quality /opt/p3-app/data/pair_quality.json \
  --json-out /tmp/xgu-index-coverage.json
```

`server-opt/index_coverage_review.py` раскладывает физические пары на cohorts:

- `open_with_signal`;
- `open_without_signal`;
- `open_with_signal_quality_fail`;
- `closed_with_signal_quality_ready`;
- `closed_with_signal_quality_fail`;
- `closed_with_signal_quality_unknown`;
- `closed_without_signal`.

Ключевой KPI — `open_pair_signal_coverage_ratio`. Отчёт никогда сам не меняет policy.

## 13. Review-only policy v2 и свежесть данных

`server-opt/build_pair_policy.py` пересекает search evidence и page quality. Candidate всегда `example_only=true`.

По умолчанию builder отклоняет evidence/quality старше **14 дней** и quality-report из другого evidence snapshot. Порог можно явно изменить `--max-input-age-days`.

## 14. Каннибализация

```bash
python server-opt/gsc_cannibalization_report.py \
  --days 90 \
  --out /opt/p3-app/data/gsc_cannibalization.json
```

`server-opt/gsc_cannibalization_report.py` использует GSC dimensions `query + page` и показывает competing page pairs и **same-city competing pairs**.

## 15. Review-план консолидации

```bash
python server-opt/build_cannibalization_review.py
python server-opt/build_cannibalization_review.py --apply
```

`server-opt/build_cannibalization_review.py` объединяет каннибализацию с `pair_quality.json` и предлагает основной URL на основе кликов/показов/позиции и качества.

**Redirect/canonical/noindex автоматически не меняются.** Общие запросы ещё не доказывают одинаковый интент.

## 16. Жизненный цикл whitelist

Whitelist — защита от случайного закрытия, а не пожизненная индексационная привилегия.

`server-opt/whitelist_lifecycle_report.py` показывает URL с актуальным сигналом, stale review candidates, отсутствующие в evidence и новые URL с сигналом вне whitelist. Автоматического удаления нет.

## 17. Единая очередь действий

`server-opt/seo_action_queue.py` собирает coverage, quality, crawl graph, metadata overlap и cannibalization review в одну очередь по URL.

Основные review-actions:

```text
CANNIBALIZATION_REVIEW
IMPROVE_OPEN_PAGE
IMPROVE_BEFORE_OPEN
OPEN_REVIEW
INTENT_REVIEW
INTERNAL_LINKING
CLOSE_REVIEW
QUALITY_AUDIT
KEEP
```

Очередь приоритизирует работу, но содержит `automatic_changes=false`: никакой redirect/index/noindex не применяется только на основании рейтинга.

## 18. Один полный read-only SEO snapshot

Для production-диагностики используется:

```bash
python server-opt/seo_snapshot.py \
  --root /var/www/x-gu.ru/current \
  --days 90
```

`server-opt/seo_snapshot.py` создаёт отдельный timestamped каталог в `/opt/p3-app/data/seo-snapshots/` и запускает только allowlist report/review-инструментов. Он не вызывает shrink, purge, rerender, bootstrap или deploy.

В snapshot входят search evidence, pair quality, corpus audit, crawl graph, metadata audit, index coverage, whitelist lifecycle, review-only policy v2 candidate, cannibalization report/review и финальный `seo_action_queue.json`.

Подробный операционный порядок — в `SEO_OPERATIONS.md`.

## 19. Целевой SEO-конвейер

```text
physical pages
  ↓
programmatic_seo_audit.py
  ↓
link_graph_cluster_audit.py
  ↓
metadata_intent_audit.py
  ↓
Yandex + GSC + manual evidence
  ↓
build_search_evidence.py
  ↓
whitelist_lifecycle_report.py
  ↓
pair_quality_audit.py
  ↓
index_coverage_review.py
  ↓
build_pair_policy.py
  ↓
gsc_cannibalization_report.py
  ↓
build_cannibalization_review.py
  ↓
seo_action_queue.py
  ↓
human review
  ↓
reviewed production policy v2
  ↓
release candidate + strict predeploy
  ↓
production
```

## 20. KPI

Смотрим не на количество созданных страниц, а на:

- physical / policy-open / indexed URLs;
- `open_pair_signal_coverage_ratio`;
- open pairs without current evidence;
- closed pairs with signal and clean quality;
- impressions, clicks, median position;
- thin/orphan/unreachable/deep pages;
- exact/near-duplicate and same-service cross-city clusters;
- services with metadata template risk;
- same-city cross-service metadata overlap;
- queries with multiple competing pages;
- stale whitelist review candidates;
- leads/conversions и search-engine exclusions.

## 21. Практический принцип x-gu.ru

**Генерировать можно десятки тысяч страниц. Индексировать нужно конкретные URL с отдельным интентом, качеством, нормальной внутренней доступностью и доказательствами ценности. Текущая policy должна регулярно доказывать свою полезность данными, а не жить бессрочно только потому, что URL когда-то был открыт.**
