# X-GU.RU — SEO operations для тысяч страниц

Этот документ описывает read-only цикл анализа production. Он не заменяет `server-opt/PRODUCTION_DEPLOY_RUNBOOK.md` и не применяет index/noindex, redirects, canonicals или policy автоматически.

## Один полный SEO-снимок

На сервере с доступом к private backend / Яндекс Вебмастер / Google Search Console:

```bash
python server-opt/seo_snapshot.py \
  --root /var/www/x-gu.ru/current \
  --days 90
```

По умолчанию создаётся отдельный timestamped каталог:

```text
/opt/p3-app/data/seo-snapshots/YYYYmmddTHHMMSSZ/
```

`server-opt/seo_snapshot.py` использует только allowlist report/review-инструментов. Он **не вызывает** `shrink_index.py`, purge, rerender, bootstrap или deploy.

В snapshot входят:

- `search_evidence.json` — объединённые Yandex/GSC/manual signals;
- `whitelist.candidate.txt` — review-only protected URL candidate;
- `pair_quality.json` — качество страниц с поисковым сигналом;
- `programmatic_seo.json` — состояние полного корпуса;
- `link_graph_cluster.json` — crawl depth и same-service content clusters;
- `metadata_intent.json` — похожесть Title/H1/Description и cross-service intent overlap;
- `index_coverage.json` — exact cohorts текущей policy;
- `whitelist_lifecycle.json` — актуальность whitelist;
- `index_policy.v2.candidate.json` — review-only exact-pair policy;
- `index_policy.v2.review.json` — причины включения/отклонения пар;
- `gsc_cannibalization.json` — query→page conflicts;
- `gsc_opportunities.json` — страницы с быстрым потенциалом роста и закрытые URL с GSC-сигналом;
- `cannibalization.review.json` — review-план конкурирующих URL;
- `seo_action_queue.json` — итоговая очередь действий;
- `snapshot_manifest.json` — дата, tooling Git SHA, root и статус каждого снимка.

Если любой этап падает, snapshot получает `status=failed` и имя failed step. Последующие этапы не запускаются.

## Итоговая очередь действий

`server-opt/seo_action_queue.py` объединяет coverage, качество, crawl graph, metadata/intent overlap, GSC opportunities и cannibalization review.

Основные действия:

- `CANNIBALIZATION_REVIEW` — несколько URL одного города делят запросы; сначала проверить интент;
- `IMPROVE_OPEN_PAGE` — URL уже индексируется и имеет спрос, но у страницы жёсткий quality defect;
- `IMPROVE_BEFORE_OPEN` — спрос есть у закрытого URL, но качество пока не позволяет открывать;
- `EVIDENCE_MISMATCH_REVIEW` — query+page GSC видит сигнал у закрытого URL, а основной evidence/coverage его не подтверждает; сначала проверить расхождение источников;
- `OPEN_REVIEW` — закрытая пара имеет search signal и достаточное качество;
- `INTENT_REVIEW` — мета/поисковое обещание подозрительно пересекается с другой услугой;
- `INTERNAL_LINKING` — открытый URL недостижим от главной или слишком глубокий;
- `SNIPPET_REVIEW` — страница уже находится высоко, но CTR заметно слабее заданного порога;
- `STRIKING_DISTANCE` — URL находится примерно на позициях 8–20 и имеет показы; кандидат на усиление;
- `CONTENT_GROWTH` — URL имеет поисковую видимость, но находится глубже и требует содержательного усиления;
- `CLOSE_REVIEW` — URL открыт, но в текущем evidence window не имеет qualifying signal;
- `QUALITY_AUDIT` — спрос есть, но quality evidence отсутствует;
- `KEEP` — текущее состояние не требует более приоритетного review.

**Это очередь для человека, а не автомат SEO-изменений.** `CLOSE_REVIEW` не означает «сразу noindex», а `OPEN_REVIEW` не означает «сразу index`.

## Как читать `index_coverage_review.py`

`server-opt/index_coverage_review.py` отвечает на самый важный вопрос для programmatic SEO: насколько текущая policy доказана текущими данными.

Ключевые cohorts:

```text
open_with_signal
open_without_signal
open_with_signal_quality_fail
closed_with_signal_quality_ready
closed_with_signal_quality_fail
closed_with_signal_quality_unknown
closed_without_signal
```

Ключевой KPI:

```text
open_pair_signal_coverage_ratio
```

Например, если открыто 700 pair pages, а текущий search/manual signal есть только у 210, coverage ratio = 0.30. Это не команда закрыть остальные 490 страниц, но сильный повод проверить их по более длинному окну, сезонности, backlinks, конверсиям и истории.

## GSC opportunities

`server-opt/gsc_opportunity_report.py` использует GSC в разрезе `query + page` и ранжирует страницы по практической возможности роста.

Основные категории:

- `SNIPPET_REVIEW` — много показов, позиция уже в топ-10, CTR ниже порога; сначала проверяются Title/Description, SERP-интент и соответствие сниппета запросам;
- `STRIKING_DISTANCE` — средняя позиция примерно 8–20; обычно это хороший кандидат на усиление контента, внутренних ссылок и интента;
- `CONTENT_GROWTH` — видимость уже есть, но позиция глубже;
- `CLOSED_SIGNAL_REVIEW` — policy закрывает URL, но Google продолжает показывать его по запросам; это review-сигнал, не команда автоматически открыть страницу.

Opportunity-report не меняет robots, canonical, policy или content.

Если `CLOSED_SIGNAL_REVIEW` противоречит `search_evidence/index_coverage`, итоговая очередь повышает URL до `EVIDENCE_MISMATCH_REVIEW`, чтобы расхождение не потерялось.

## Метаданные и интенты

`server-opt/metadata_intent_audit.py` измеряет, насколько Title/H1/Description похожи:

- у одной услуги по разным городам;
- у разных услуг внутри одного города.

Цель — не искусственная текстовая «уникализация». Если две страницы реально отвечают одному интенту, надо решать архитектуру URL/контента. Если интенты разные, различия должны быть содержательными, а не случайной перестановкой слов.

## Каннибализация

`server-opt/gsc_cannibalization_report.py` использует GSC `query + page` и показывает запросы, по которым участвует несколько URL.

`server-opt/build_cannibalization_review.py` предлагает основной URL только как review-подсказку и учитывает реальную search performance + `pair_quality.json`.

Даже сильная рекомендация не является основанием автоматически ставить redirect: сначала проверяются интент, conversions, backlinks и бизнес-назначение страниц.

## Жизненный цикл whitelist

`server-opt/whitelist_lifecycle_report.py` регулярно проверяет, остаются ли защищённые URL обоснованными текущими данными.

Категории stale/missing evidence — только review. Перед снятием whitelist-защиты проверяются внешние ссылки, лиды, исторические запросы, сезонность и manual business requirements.

## Свежесть данных

`build_pair_policy.py` по умолчанию не принимает evidence/quality старше 14 дней и требует, чтобы quality-report был построен из того же evidence snapshot.

Это защищает от ситуации, когда новая production policy строится по случайно найденному старому JSON.

## Безопасный цикл изменений

```text
seo_snapshot.py
  ↓
review seo_action_queue.json
  ↓
ручные решения по OPEN/CLOSE/IMPROVE/GROWTH/CANNIBALIZATION
  ↓
reviewed index_policy.json v2
  ↓
отдельный release candidate
  ↓
shrink/rerender только candidate
  ↓
finalize + strict predeploy
  ↓
atomic deploy
  ↓
новый SEO snapshot после достаточного периода данных
```

Production rollout выполняется только по `server-opt/PRODUCTION_DEPLOY_RUNBOOK.md`.
