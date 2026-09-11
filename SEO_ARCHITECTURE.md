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

Policy v1 — историческая матрица `open_cities × open_services`.

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

`server-opt/link_graph_cluster_audit.py` показывает достижимость от главной, crawl depth и exact/near-duplicate кластеры одной услуги по разным городам.

## 9. Шаблонность Title/H1/Description и пересечение интентов

Исторический generator строит Title по формуле вида `{service} в {city} | СЕО ГУРУ`, а Description использует общую региональную формулу. Это не означает автоматическую SEO-ошибку, но создаёт сильный шаблонный отпечаток при массовом масштабировании.

Поэтому используется отдельный read-only аудит:

```bash
python server-opt/metadata_intent_audit.py \
  --root /var/www/x-gu.ru/current \
  --json-out /tmp/xgu-metadata-intent.json
```

Он измеряет похожесть Title/H1/Description и отдельно показывает:

- страницы одной услуги по разным городам с очень похожими мета;
- услуги, где большая доля всех городских пар выглядит как один мета-шаблон;
- разные service pages одного города с подозрительно похожими мета/обещанием — возможный intent-overlap и будущая каннибализация.

Высокая похожесть — сигнал для ревью. Не надо искусственно рандомить Title ради «уникальности»: сначала проверяется реальный интент и качество страницы.

## 10. Сначала поисковые данные, потом shrink

`server-opt/build_search_evidence.py` объединяет Яндекс Вебмастер, Google Search Console и manual protected URLs.

Результат:

```text
/opt/p3-app/data/search_evidence.json
/opt/p3-app/data/whitelist.candidate.txt
```

Скрипт не меняет production whitelist автоматически. GSC собирается с `startRow` pagination.

## 11. Quality audit страниц с поисковым сигналом

```bash
python server-opt/pair_quality_audit.py \
  --root /var/www/x-gu.ru/current \
  --evidence /opt/p3-app/data/search_evidence.json \
  --out /opt/p3-app/data/pair_quality.json
```

Жёсткие дефекты переводят страницу в `improve_before_index`. Near-duplicate получает `review_similarity`, а не автоматический запрет.

Quality report хранит `generated_at` и `source_evidence_generated_at`, чтобы дальнейшее решение было связано с конкретным снимком поисковых данных.

## 12. Review-only policy v2 и свежесть данных

`server-opt/build_pair_policy.py` пересекает search evidence и page quality:

```bash
python server-opt/build_pair_policy.py
python server-opt/build_pair_policy.py --apply
```

Candidate всегда содержит `"example_only": true`, поэтому не может быть случайно применён как production policy.

По умолчанию builder отказывается использовать evidence/quality старше **14 дней** и отклоняет quality-report, если он был построен из другого evidence snapshot. Порог можно явно изменить через `--max-input-age-days`, но устаревшие данные не проходят незаметно.

## 13. Каннибализация

```bash
python server-opt/gsc_cannibalization_report.py \
  --days 90 \
  --out /opt/p3-app/data/gsc_cannibalization.json
```

`server-opt/gsc_cannibalization_report.py` запрашивает GSC в разрезе `query + page` и показывает affected pages, competing page pairs и отдельные **same-city competing pairs**.

## 14. Review-план консолидации

```bash
python server-opt/build_cannibalization_review.py
python server-opt/build_cannibalization_review.py --apply
```

`server-opt/build_cannibalization_review.py` объединяет каннибализацию с `pair_quality.json` и предлагает основной URL на основе реальных кликов/показов/позиции и качества страницы.

**Redirect/canonical/noindex автоматически не меняются.** Общие запросы ещё не доказывают одинаковый интент.

## 15. Жизненный цикл whitelist

Whitelist — это защита от случайного закрытия, а не пожизненная индексационная привилегия.

```bash
python server-opt/whitelist_lifecycle_report.py \
  --whitelist /var/www/x-gu.ru/current/.xgu-whitelist.txt \
  --evidence /opt/p3-app/data/search_evidence.json \
  --json-out /tmp/xgu-whitelist-lifecycle.json
```

Отчёт показывает URL с актуальным сигналом, stale review candidates, отсутствующие в текущем evidence и новые URL с сигналом вне whitelist.

**Автоматического удаления нет.** Перед снятием защиты проверяются backlinks, конверсии, бизнес-критичность и исторические данные.

## 16. Целевой SEO-конвейер

```text
десятки тысяч физических страниц
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

## 17. KPI

Смотрим не на количество созданных страниц, а на:

- physical / policy-open / indexed URLs;
- impressions, clicks и median position;
- долю open URL с 0 impressions за 28/90 дней;
- thin/orphan/unreachable/deep pages;
- exact/near-duplicate pages и same-service cross-city clusters;
- services with metadata template risk;
- same-city cross-service metadata overlap;
- `open → closed` links;
- queries with multiple competing pages;
- same-city competing pairs;
- stale whitelist review candidates;
- leads/conversions по landing pages;
- low-value/duplicate/crawled-not-indexed exclusions.

## 18. Практический принцип x-gu.ru

**Генерировать можно десятки тысяч страниц. Индексировать нужно только конкретные URL с отдельным поисковым интентом, достаточным качеством, нормальной внутренней доступностью и доказательствами ценности. Если несколько URL делят один интент — сначала измерить каннибализацию, затем вручную решить, разводить интенты или консолидировать страницы.**
