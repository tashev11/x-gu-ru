from __future__ import annotations
import csv
import hashlib
import json
import shutil
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.city import City
from app.models.service import Service
from app.models.site import Site
from app.models.site_page import SitePage
from app.services.keyword_profile_service import build_keyword_profile
from app.services.seo_service import generate_sitemaps


def _template_env() -> Environment:
    return Environment(loader=FileSystemLoader("app/templates"), autoescape=False)


@lru_cache(maxsize=1)
def _city_slug_to_ru_name() -> dict[str, str]:
    mapping: dict[str, str] = {}
    csv_path = Path("data/ru_cities_with_population.csv")
    if not csv_path.exists():
        return mapping
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            slug = (row.get("slug") or "").strip()
            city = (row.get("city") or "").strip()
            if slug and city:
                mapping[slug] = city
    return mapping


@lru_cache(maxsize=1)
def _service_slug_to_ru_name() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for csv_name in ("data/keywords_all.csv", "data/keywords_wave_2.csv", "data/keywords_100.csv"):
        csv_path = Path(csv_name)
        if not csv_path.exists():
            continue
        with csv_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                slug = (row.get("slug") or "").strip()
                name = (row.get("name") or "").strip()
                if slug and name:
                    mapping[slug] = name
    return mapping


@lru_cache(maxsize=1)
def _service_slug_to_cluster() -> dict[str, str]:
    """slug -> cluster (seo|webdev|ads|audit) for grouping service links on hubs."""
    mapping: dict[str, str] = {}
    for csv_name in ("data/keywords_all.csv", "data/keywords_wave_2.csv", "data/keywords_100.csv"):
        csv_path = Path(csv_name)
        if not csv_path.exists():
            continue
        with csv_path.open(encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                slug = (row.get("slug") or "").strip()
                cluster = (row.get("cluster") or "").strip().lower()
                if slug and cluster:
                    mapping[slug] = cluster
    return mapping


@lru_cache(maxsize=1)
def _load_keep_config() -> dict | None:
    """Index-shrink config (2026-08-14): which city/service combinations stay
    open for indexing. Returns None when the config is absent (pre-shrink
    behaviour: everything open). whitelist.txt pages are always open.

    Shape: {"open_cities": set, "open_services": set,
            "whitelist_paths": set of (city_slug, service_slug|None)}
    """
    cfg_path = Path("data/index_keep_config.json")
    if not cfg_path.exists():
        return None
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    whitelist_paths: set[tuple[str, str | None]] = set()
    wl = Path("data/whitelist.txt")
    if wl.exists():
        for line in wl.read_text(encoding="utf-8").splitlines():
            u = line.strip()
            if not u:
                continue
            parts = [p for p in u.split("x-gu.ru", 1)[-1].split("/") if p]
            if len(parts) == 1:
                whitelist_paths.add((parts[0], None))
            elif len(parts) >= 2:
                whitelist_paths.add((parts[0], parts[1]))
    return {
        "open_cities": set(cfg.get("open_cities") or []),
        "open_services": set(cfg.get("open_services") or []),
        "whitelist_paths": whitelist_paths,
    }


def _page_is_open(city_slug: str, service_slug: str | None = None) -> bool:
    """True if the page should be indexable (in sitemap, robots=index)."""
    keep = _load_keep_config()
    if keep is None:
        return True
    if (city_slug, service_slug) in keep["whitelist_paths"]:
        return True
    if city_slug not in keep["open_cities"]:
        return False
    return service_slug is None or service_slug in keep["open_services"]


ROBOTS_INDEX = "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"
ROBOTS_NOINDEX = "noindex, follow"


@lru_cache(maxsize=1)
def _homepage_cities() -> list[dict]:
    """Cities for the homepage grid: Cyrillic name + slug, only pop>=100000
    (the deployed set), sorted by Russian name. Avoids Latin transliteration
    and dead links to non-existent city pages."""
    rows: list[dict] = []
    csv_path = Path("data/ru_cities_with_population.csv")
    if not csv_path.exists():
        return rows
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                pop = int((row.get("population") or "0").replace(" ", "").replace("\xa0", ""))
            except Exception:
                pop = 0
            slug = (row.get("slug") or "").strip()
            name = (row.get("city") or "").strip()
            if slug and name and pop >= 100000:
                rows.append({"name": name, "slug": slug})
    keep = _load_keep_config()
    if keep is not None:
        rows = [r for r in rows if r["slug"] in keep["open_cities"]]
    rows.sort(key=lambda x: x["name"].lower())
    return rows


def _city_prepositional(city_name: str) -> str:
    irregular = {
        "Москва": "Москве",
        "Санкт-Петербург": "Санкт-Петербурге",
        "Нижний Новгород": "Нижнем Новгороде",
        "Великий Новгород": "Великом Новгороде",
        "Орёл": "Орле",
        "Орел": "Орле",
        "Йошкар-Ола": "Йошкар-Оле",
        "Набережные Челны": "Набережных Челнах",
        "Минеральные Воды": "Минеральных Водах",
        "Великие Луки": "Великих Луках",
        "Ярославль": "Ярославле",
        "Севастополь": "Севастополе",
        "Ставрополь": "Ставрополе",
    }
    if city_name in irregular:
        return irregular[city_name]
    for sep in ("-на-", "-над-", "-под-"):
        if sep in city_name:
            head, _, tail = city_name.partition(sep)
            return _city_prepositional(head) + sep + tail
    if city_name.endswith("ия"):
        return city_name[:-2] + "ии"
    if city_name.endswith("ый"):
        return city_name[:-2] + "ом"
    if city_name.endswith("ий"):
        return city_name[:-2] + "ем"
    if city_name.endswith("ой"):
        return city_name[:-2] + "ом"
    if city_name.endswith("а"):
        return city_name[:-1] + "е"
    if city_name.endswith("я"):
        return city_name[:-1] + "е"
    if city_name.endswith("ь"):
        return city_name[:-1] + "и"
    if city_name.endswith("ы"):
        return city_name[:-1] + "ах"
    if city_name.endswith(("о", "е", "и", "у", "ю", "э", "ё")):
        return city_name
    return city_name + "е"


def _trim_for_title(text: str, max_len: int = 75) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _pad_title_min(title: str, suffix: str, extra: str, min_len: int = 30) -> str:
    """Ensure title meets the SEO minimum length by inserting `extra` before
    `suffix` (e.g. ' и регионе' before ' | СЕО ГУРУ'). No-op if already long
    enough or suffix isn't present. Keeps short city names like Уфа/Тула/Сочи
    from producing sub-30-char titles."""
    if len(title) >= min_len or suffix not in title:
        return title
    head, _, tail = title.partition(suffix)
    return f"{head}{extra}{suffix}{tail}"


def _trim_for_description(text: str, max_len: int = 180) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _review_variant(city_slug: str, service_slug: str, city_pp: str, service_name: str) -> dict:
    authors = ["Алексей", "Марина", "Игорь", "Екатерина", "Дмитрий", "Ольга"]
    templates = [
        "За {service} в {city} получили стабильный поток заявок. Работа прозрачная и по этапам.",
        "После запуска {service} в {city} выросли обращения из поиска, отчётность понятная каждую неделю.",
        "Команда усилила {service} в {city}: вырос трафик и снизилась стоимость заявки.",
        "Нужен был результат по {service} в {city} — получили рост позиций и лидов без лишних обещаний.",
        "По направлению {service} в {city} сделали структуру и контент, заявки пошли в плановые сроки.",
    ]
    ratings = ["5", "5", "5", "4", "5"]
    seed = hashlib.sha256(f"{city_slug}:{service_slug}".encode("utf-8")).hexdigest()
    idx = int(seed[:8], 16)
    author = authors[idx % len(authors)]
    body_tpl = templates[idx % len(templates)]
    rating = ratings[idx % len(ratings)]
    return {
        "author": author,
        "rating": rating,
        "body": body_tpl.format(service=service_name.lower(), city=city_pp),
    }


def _landing_variants(service_name: str, service_slug: str, city_name: str, city_pp: str, region: str) -> dict:
    seed = int(hashlib.sha256(f"{city_name}:{service_slug}".encode("utf-8")).hexdigest()[:8], 16)
    cluster = build_keyword_profile(service_name, service_slug, city_pp, region)["cluster"]

    audience_pool = {
        "seo": [
            ("Локальный бизнес", f"Усиливаем спрос по запросу «{service_name.lower()}» в {city_pp} и соседних районах."),
            ("Компании услуг", "Собираем структуру под коммерческие интенты и убираем потери на пустом трафике."),
            ("Региональные бренды", f"Закрываем геозависимые запросы и усиливаем видимость по {region}."),
            ("Сайты после редизайна", "Возвращаем индексацию, корректируем структуру и коммерческие факторы."),
        ],
        "webdev": [
            ("Новый проект", "Собираем сайт с учетом SEO-архитектуры и дальнейшего роста трафика."),
            ("Услуги и B2B", "Делаем продающие страницы, формы заявок и понятную структуру оффера."),
            ("Региональный запуск", f"Готовим городские страницы под спрос в {city_pp}."),
            ("Редизайн", "Пересобираем старый сайт без потери поискового потенциала."),
        ],
        "ads": [
            ("Быстрый старт лидов", "Запускаем кампании с понятной экономикой и привязкой к заявкам."),
            ("Компании с дорогим лидом", "Пересобираем семантику, цели и посадочные для снижения CPL."),
            ("Локальный бизнес", f"Комбинируем рекламу и SEO под спрос в {city_pp}."),
            ("Проекты без аналитики", "Ставим цели, calltracking и сквозной контроль качества трафика."),
        ],
        "audit": [
            ("Сайты с просадкой", "Ищем причины падения трафика, индексации и заявок."),
            ("Новые сайты", "Проверяем базовую технику до старта масштабирования."),
            ("Старые проекты", "Собираем список ошибок и очередность внедрения по влиянию на бизнес."),
            ("Команды in-house", "Даем внешний аудит и понятный backlog доработок."),
        ],
    }

    deliverables_pool = {
        "seo": [
            "Семантическое ядро и кластеризация по интентам.",
            "Структура посадочных страниц под город и услугу.",
            "ТЗ на контент, мета-теги и коммерческие блоки.",
            "Контроль индексации, перелинковки и роста видимости.",
        ],
        "webdev": [
            "Прототип и структура сайта под лидогенерацию.",
            "Адаптивная верстка и быстрые посадочные страницы.",
            "Формы заявок, аналитика и базовая SEO-разметка.",
            "Подготовка к масштабированию по городам и ключам.",
        ],
        "ads": [
            "Сбор семантики и карта рекламных кампаний.",
            "Настройка целей, аналитики и calltracking.",
            "Тест гипотез по объявлениям и посадочным.",
            "Оптимизация CPL и отчеты по заявкам.",
        ],
        "audit": [
            "Список критичных технических ошибок.",
            "Приоритизация доработок по влиянию на заявки.",
            "Чек-лист по индексации, скорости и структуре.",
            "План внедрения на 30-60 дней.",
        ],
    }

    faq_pool = {
        "seo": [
            ("Что влияет на рост по этому запросу?", "Решающими будут структура страниц, коммерческие факторы, индексируемый контент и качество внутренних связей между страницами."),
            ("Нужно ли отдельное продвижение под город?", "Да. Поисковик лучше понимает отдельную страницу с геосигналами, локальным оффером и своим набором коммерческих формулировок."),
            ("Когда пересматривать семантику?", "Обычно раз в 4-8 недель: часть запросов усиливается, часть теряет спрос, часть требует новых посадочных."),
        ],
        "webdev": [
            ("Почему нельзя делать сайт отдельно от SEO?", "Потому что потом приходится переделывать структуру, блоки и логику страниц. Дешевле и быстрее заложить SEO-каркас сразу."),
            ("Что важнее для сайта услуг?", "Понятный оффер, сильные коммерческие блоки, микроразметка, скорость и страницы под реальные интенты спроса."),
            ("Можно ли масштабировать сайт дальше?", "Да. Текущая структура рассчитана на добавление новых городов, запросов и ниш без полной переделки."),
        ],
        "ads": [
            ("Почему вы связываете рекламу и SEO?", "Потому что посадочная влияет на стоимость клика и на конверсию. Одни и те же страницы должны работать и в поиске, и в рекламе."),
            ("Когда будет понятен CPL?", "Обычно после первого набора данных: 7-14 дней при достаточном объеме трафика."),
            ("Как контролировать качество заявок?", "Через цели, CRM, calltracking и разметку источников по каждому каналу."),
        ],
        "audit": [
            ("Что вы считаете критичной ошибкой?", "То, что блокирует индексацию, ломает структуру спроса, режет конверсию или мешает поисковику понимать страницу."),
            ("Аудит заканчивается списком ошибок?", "Нет. Итогом должен быть план внедрения с приоритетами, сроками и ожидаемым эффектом."),
            ("Нужно ли внедрять все сразу?", "Нет. Сначала исправляются точки максимального влияния на индекс, трафик и заявки."),
        ],
    }

    review_pool = {
        "seo": [
            {
                "name": "Алексей",
                "role": "автосервис",
                "rating": "4.9/5",
                "duration": "5 месяцев работы",
                "before_label": "До",
                "before_main": "3 лида/мес",
                "before_sub": "трафик 210",
                "after_label": "После",
                "after_main": "23 лида/мес",
                "after_sub": "трафик 1 120",
                "quote": "Вижу рост по отчетам и понимаю, какие страницы реально приводят заявки.",
                "avatar": "https://randomuser.me/api/portraits/men/32.jpg",
            },
            {
                "name": "Марина",
                "role": "стоматология",
                "rating": "5.0/5",
                "duration": "6 месяцев работы",
                "before_label": "До",
                "before_main": "5 записей/мес",
                "before_sub": "CTR 1.2%",
                "after_label": "После",
                "after_main": "27 записей/мес",
                "after_sub": "CTR 3.9%",
                "quote": "Получила доступ к дашборду и отчетам. Видно, как растут позиции и обращения.",
                "avatar": "https://randomuser.me/api/portraits/women/44.jpg",
            },
            {
                "name": "Дмитрий",
                "role": "B2B услуги",
                "rating": "4.8/5",
                "duration": "4 месяца работы",
                "before_label": "До",
                "before_main": "4 заявки/мес",
                "before_sub": "CPL 4 900 ₽",
                "after_label": "После",
                "after_main": "19 заявок/мес",
                "after_sub": "CPL 2 700 ₽",
                "quote": "Все цифры подтверждены отчетами: вижу воронку, источники и реальную динамику.",
                "avatar": "https://randomuser.me/api/portraits/men/46.jpg",
            },
            {
                "name": "Ольга",
                "role": "юридические услуги",
                "rating": "5.0/5",
                "duration": "4 месяца работы",
                "before_label": "До",
                "before_main": "2 лида/мес",
                "before_sub": "видимость 18 запросов",
                "after_label": "После",
                "after_main": "17 лидов/мес",
                "after_sub": "видимость 74 запросов",
                "quote": "Наконец появились понятные отчеты и прогноз по росту, а не обещания без цифр.",
                "avatar": "https://randomuser.me/api/portraits/women/63.jpg",
            },
        ],
        "webdev": [
            {
                "name": "Игорь",
                "role": "производственная компания",
                "rating": "5.0/5",
                "duration": "7 недель работы",
                "before_label": "До",
                "before_main": "1 форма/мес",
                "before_sub": "сайт устарел",
                "after_label": "После",
                "after_main": "14 форм/мес",
                "after_sub": "скорость 92/100",
                "quote": "Сайт сразу собрали с логикой под SEO и заявки. Переделывать после запуска не пришлось.",
                "avatar": "https://randomuser.me/api/portraits/men/15.jpg",
            },
            {
                "name": "Екатерина",
                "role": "медицинские услуги",
                "rating": "4.9/5",
                "duration": "2 месяца работы",
                "before_label": "До",
                "before_main": "0 заявок",
                "before_sub": "старый лендинг",
                "after_label": "После",
                "after_main": "18 заявок/мес",
                "after_sub": "конверсия 3.6%",
                "quote": "Получили понятную структуру страниц, формы, аналитику и сразу базу для продвижения.",
                "avatar": "https://randomuser.me/api/portraits/women/25.jpg",
            },
            {
                "name": "Алексей",
                "role": "строительная компания",
                "rating": "4.8/5",
                "duration": "6 недель работы",
                "before_label": "До",
                "before_main": "1 страница",
                "before_sub": "без SEO-структуры",
                "after_label": "После",
                "after_main": "11 страниц",
                "after_sub": "готово к рекламе",
                "quote": "Сразу получил сайт, который можно масштабировать по городам и направлениям без новой сборки.",
                "avatar": "https://randomuser.me/api/portraits/men/51.jpg",
            },
            {
                "name": "Марина",
                "role": "образовательный проект",
                "rating": "5.0/5",
                "duration": "5 недель работы",
                "before_label": "До",
                "before_main": "3 обращения/мес",
                "before_sub": "медленный сайт",
                "after_label": "После",
                "after_main": "21 обращение/мес",
                "after_sub": "скорость x2",
                "quote": "Сделали не просто красивый сайт, а рабочую систему под заявки и дальнейшее SEO.",
                "avatar": "https://randomuser.me/api/portraits/women/36.jpg",
            },
        ],
        "ads": [
            {
                "name": "Дмитрий",
                "role": "сервисная компания",
                "rating": "4.9/5",
                "duration": "2 месяца работы",
                "before_label": "До",
                "before_main": "CPL 5 300 ₽",
                "before_sub": "нет сквозной аналитики",
                "after_label": "После",
                "after_main": "CPL 2 800 ₽",
                "after_sub": "12 лидов/мес",
                "quote": "После настройки рекламы и посадочных стало понятно, какие кампании реально приносят продажи.",
                "avatar": "https://randomuser.me/api/portraits/men/27.jpg",
            },
            {
                "name": "Ольга",
                "role": "клиника",
                "rating": "5.0/5",
                "duration": "10 недель работы",
                "before_label": "До",
                "before_main": "7 заявок/мес",
                "before_sub": "дорогой клик",
                "after_label": "После",
                "after_main": "24 заявки/мес",
                "after_sub": "CTR 4.1%",
                "quote": "В отчетах видно объявления, стоимость заявки и вклад каждой страницы в результат.",
                "avatar": "https://randomuser.me/api/portraits/women/41.jpg",
            },
            {
                "name": "Игорь",
                "role": "локальный ритейл",
                "rating": "4.8/5",
                "duration": "8 недель работы",
                "before_label": "До",
                "before_main": "3 обращения/нед",
                "before_sub": "реклама без целей",
                "after_label": "После",
                "after_main": "11 обращений/нед",
                "after_sub": "ROMI растет",
                "quote": "Перестали сливать бюджет вслепую. Появилась нормальная аналитика и понятные гипотезы.",
                "avatar": "https://randomuser.me/api/portraits/men/64.jpg",
            },
            {
                "name": "Екатерина",
                "role": "онлайн-школа",
                "rating": "5.0/5",
                "duration": "9 недель работы",
                "before_label": "До",
                "before_main": "CPL 3 900 ₽",
                "before_sub": "мало лидов",
                "after_label": "После",
                "after_main": "CPL 2 200 ₽",
                "after_sub": "рост конверсии",
                "quote": "Связка рекламы и посадочных дала ощутимый результат уже на первой итерации.",
                "avatar": "https://randomuser.me/api/portraits/women/58.jpg",
            },
        ],
        "audit": [
            {
                "name": "Марина",
                "role": "интернет-магазин",
                "rating": "5.0/5",
                "duration": "3 недели работы",
                "before_label": "До",
                "before_main": "17 ошибок",
                "before_sub": "просадка индексации",
                "after_label": "После",
                "after_main": "2 критичных",
                "after_sub": "рост видимости",
                "quote": "Получили не просто список ошибок, а понятный порядок внедрения и эффект по каждому блоку.",
                "avatar": "https://randomuser.me/api/portraits/women/12.jpg",
            },
            {
                "name": "Алексей",
                "role": "услуги для бизнеса",
                "rating": "4.9/5",
                "duration": "1 месяц работы",
                "before_label": "До",
                "before_main": "скорость 38/100",
                "before_sub": "нет приоритетов",
                "after_label": "После",
                "after_main": "скорость 79/100",
                "after_sub": "план внедрения готов",
                "quote": "Аудит помог понять, что реально мешает росту, и не тратить время на второстепенные доработки.",
                "avatar": "https://randomuser.me/api/portraits/men/19.jpg",
            },
            {
                "name": "Дмитрий",
                "role": "корпоративный сайт",
                "rating": "4.8/5",
                "duration": "18 дней работы",
                "before_label": "До",
                "before_main": "0 ясности",
                "before_sub": "трафик падал",
                "after_label": "После",
                "after_main": "54 задачи",
                "after_sub": "план на 60 дней",
                "quote": "После аудита команда внедряла правки уже по приоритетам, а не вслепую.",
                "avatar": "https://randomuser.me/api/portraits/men/53.jpg",
            },
            {
                "name": "Ольга",
                "role": "медицинский проект",
                "rating": "5.0/5",
                "duration": "20 дней работы",
                "before_label": "До",
                "before_main": "11 точек риска",
                "before_sub": "структура ломала индекс",
                "after_label": "После",
                "after_main": "3 приоритета",
                "after_sub": "внедрение без хаоса",
                "quote": "Главная ценность аудита — ясная карта работ и прозрачная логика, что исправлять сначала.",
                "avatar": "https://randomuser.me/api/portraits/women/29.jpg",
            },
        ],
    }

    proof_pool = {
        "seo": [
            "Доступ к позициям, лидам и динамике видимости по кластерам.",
            "Ежемесячный отчет по росту органики и конверсии страниц.",
            f"Отдельный контроль геозависимых запросов в {city_pp}.",
            "Фиксация доработок по коммерческим факторам и структуре.",
        ],
        "webdev": [
            "Прототип, структура и список обязательных коммерческих блоков.",
            "Технический чек-лист по скорости, формам и адаптивности.",
            "Отчет по готовности сайта к SEO и рекламному трафику.",
            f"План расширения структуры под новые страницы в {city_pp}.",
        ],
        "ads": [
            "Дашборд по CPL, заявкам и качеству обращений.",
            "Отдельный срез по объявлениям, группам и посадочным.",
            "Помесячная таблица расходов и результата по каналам.",
            "Контроль целей, звонков и UTM-разметки без слепых зон.",
        ],
        "audit": [
            "Список критичных ошибок с приоритетом внедрения.",
            "Чек-лист индексации, скорости, структуры и коммерческих факторов.",
            "Карта доработок на 30-60 дней с ожидаемым эффектом.",
            "Отдельный блок по тому, что можно отложить без риска для роста.",
        ],
    }

    audience_cards = audience_pool[cluster]
    deliverables = deliverables_pool[cluster]
    extra_faq = faq_pool[cluster]
    reviews = review_pool[cluster]
    proof_points = proof_pool[cluster]

    offset = seed % len(audience_cards)
    audience_cards = audience_cards[offset:] + audience_cards[:offset]
    deliverables = deliverables[offset % len(deliverables):] + deliverables[: offset % len(deliverables)]
    extra_faq = extra_faq[offset % len(extra_faq):] + extra_faq[: offset % len(extra_faq)]
    reviews = reviews[offset % len(reviews):] + reviews[: offset % len(reviews)]
    proof_points = proof_points[offset % len(proof_points):] + proof_points[: offset % len(proof_points)]

    return {
        "audience_cards": [{"title": title, "text": text} for title, text in audience_cards[:3]],
        "deliverables": deliverables[:4],
        "review_cards": reviews[:3],
        "proof_points": proof_points[:4],
        "faq_items": [
            {
                "question": "Когда будут первые заявки?",
                "answer": "Первые сигналы обычно появляются через 2-3 недели, а стабильный поток формируется на горизонте 2-4 месяцев в зависимости от ниши и конкуренции.",
            },
            {
                "question": "Сколько стоит продвижение?",
                "answer": "Аудит и консультация бесплатны. Базовый пакет стоит 15 000 ₽ в месяц, комплексный пакет — 50 000 ₽ в месяц.",
            },
            {
                "question": "Как контролировать результат?",
                "answer": "Вы получаете доступ к отчетам по позициям, индексации, трафику и заявкам. Метрики привязываются к целям, а не к абстрактной активности.",
            },
            *[{"question": q, "answer": a} for q, a in extra_faq[:3]],
        ],
    }


def _render_city_hub_html(city: City, services: list[Service]) -> str:
    # Index-shrink: keep only open services (+ this city's whitelist pages)
    # in the chip navigation, and noindex hubs of closed cities.
    keep = _load_keep_config()
    if keep is not None:
        wl_services = {s for (c, s) in keep["whitelist_paths"] if c == city.slug and s}
        services = [
            s for s in services
            if s.slug in keep["open_services"] or s.slug in wl_services
        ]
    hub_robots = ROBOTS_INDEX if _page_is_open(city.slug) else ROBOTS_NOINDEX
    city_display = _city_slug_to_ru_name().get(city.slug, city.name)
    city_pp = _city_prepositional(city_display)
    # SEO-only variant of city_pp for <title>/og:title/twitter:title/JSON-LD
    # name: short city names (Уфа, Тула, Сочи, Орёл...) make "SEO-услуги в
    # {city} | СЕО ГУРУ" fall under the 30-char minimum, so pad with "и
    # регионе" there. Visible H1 keeps the plain city_pp.
    city_pp_seo = city_pp
    if len(f"SEO-услуги в {city_pp} | СЕО ГУРУ") < 30:
        city_pp_seo = f"{city_pp} и регионе"
    seed = int(hashlib.sha256(city.slug.encode("utf-8")).hexdigest()[:8], 16)
    growth = 18 + (seed % 37)
    leads = 9 + (seed % 28)
    cpl_drop = 12 + (seed % 24)
    idx_days = 10 + (seed % 16)
    service_cards = [
        {
            "title": _service_slug_to_ru_name().get(service.slug, service.name).lower(),
            "href": f"/{city.slug}/{service.slug}/",
            "cluster": _service_slug_to_cluster().get(service.slug, "seo"),
        }
        for service in services
    ]
    env = _template_env()
    tpl = env.get_template("city_hub_master.html.j2")
    return tpl.render(
        base_domain=settings.base_domain,
        city_name=city_display,
        city_slug=city.slug,
        city_pp=city_pp,
        city_pp_seo=city_pp_seo,
        robots_content=hub_robots,
        growth=growth,
        leads=leads,
        cpl_drop=cpl_drop,
        idx_days=idx_days,
        service_cards_json=json.dumps(service_cards, ensure_ascii=False),
    )


def _write_preview_seo_files(
    preview_root: Path,
    urls: list[str],
    urls_per_file: int = 40000,
) -> None:
    # Боты, которым разрешено цитировать сайт: поисковики и AI-ассистенты.
    # Bytespider убран из списка — он давал 2100 запросов в сутки, а трафика
    # с него нет.
    ai_bots = (
        "OAI-SearchBot", "ChatGPT-User", "GPTBot", "ClaudeBot",
        "Claude-SearchBot", "anthropic-ai", "PerplexityBot", "Perplexity-User",
        "Google-Extended", "Googlebot", "bingbot", "YandexBot",
        "Applebot", "Applebot-Extended", "Amazonbot", "CCBot",
        "Meta-ExternalAgent", "FacebookBot", "YouBot", "DuckAssistBot",
    )
    # SEO-краулеры и агрессивные сборщики: трафика не приносят, но съедали
    # больше трети всех запросов к серверу (SemrushBot 2828/сут против
    # 215 у Googlebot). На одноядерной машине это заметно.
    blocked_bots = (
        "SemrushBot", "AhrefsBot", "MJ12bot", "DotBot", "BLEXBot",
        "DataForSeoBot", "Bytespider", "PetalBot", "SeekportBot",
        "serpstatbot", "ZoominfoBot", "Barkrowler",
    )
    robots = "# Разрешаем AI-ботам цитировать сайт (citation/search bots)\n\n"
    robots += "".join(f"User-agent: {bot}\nAllow: /\n\n" for bot in ai_bots)
    robots += "# Краулеры без пользы для трафика — закрыты ради нагрузки на сервер\n\n"
    robots += "".join(f"User-agent: {bot}\nDisallow: /\n\n" for bot in blocked_bots)
    robots += (
        "User-agent: *\n"
        "Allow: /\n\n"
        f"Sitemap: https://{settings.base_domain}/sitemap.xml\n"
    )

    # Index-shrink: only open/whitelisted URLs go to the sitemap.
    keep = _load_keep_config()
    if keep is not None:
        def _url_open(u: str) -> bool:
            parts = [p for p in u.split(settings.base_domain, 1)[-1].split("/") if p]
            if not parts:
                return True  # homepage
            if len(parts) == 1:
                return parts[0] == "privacy" or _page_is_open(parts[0])
            return _page_is_open(parts[0], parts[1])
        urls = [u for u in urls if _url_open(u)]

    chunks = [urls[i : i + urls_per_file] for i in range(0, len(urls), urls_per_file)] or [[]]
    sitemaps_dir = preview_root / "sitemaps"
    sitemaps_dir.mkdir(parents=True, exist_ok=True)

    index_parts = ['<?xml version="1.0" encoding="UTF-8"?>', '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for idx, chunk in enumerate(chunks, start=1):
        shard_name = f"sitemap-{idx}.xml"
        shard_lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
        for url in chunk:
            shard_lines.append(f"  <url><loc>{url}</loc></url>")
        shard_lines.append("</urlset>")
        (sitemaps_dir / shard_name).write_text("\n".join(shard_lines) + "\n", encoding="utf-8")
        index_parts.append(f"  <sitemap><loc>https://{settings.base_domain}/sitemaps/{shard_name}</loc></sitemap>")
    index_parts.append("</sitemapindex>")

    (preview_root / "robots.txt").write_text(robots, encoding="utf-8")
    (preview_root / "sitemap.xml").write_text("\n".join(index_parts) + "\n", encoding="utf-8")

    # Render simple but indexable homepage with links to available city/service pages.
    env = _template_env()
    homepage_tpl = env.get_template("homepage_master.html.j2")
    home_html = homepage_tpl.render(
        base_domain=settings.base_domain,
        cities_json=json.dumps(_homepage_cities(), ensure_ascii=False),
    )
    (preview_root / "index.html").write_text(home_html, encoding="utf-8")


def _render_context(site: Site, city: City, service: Service) -> dict:
    city_pp = _city_prepositional(city.name)
    title = f"{service.name} в {city_pp} - заказать на {settings.base_domain}"
    description = f"{service.name} в {city_pp}. Условия, цены и консультация на русском языке."
    canonical = f"https://{settings.base_domain}/{city.slug}/{service.slug}/"
    jsonld_payload = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": f"{service.name} в {city_pp}",
        "areaServed": city.name,
        "address": {"addressCountry": "RU", "addressLocality": city.name},
    }
    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "og_title": title,
        "og_description": description,
        "city_name": city.name,
        "service_name": service.name,
        "h1": f"{service.name} в {city_pp}",
        "body_intro": f"Предоставляем услугу \"{service.name}\" для клиентов в {city_pp}.",
        "body_value": "Работаем по прозрачным этапам и с понятными сроками.",
        "faqs": [
            {
                "question": f"Сколько стоит {service.name.lower()} в {city_pp}?",
                "answer": "Стоимость зависит от задачи. Оставьте запрос для точной оценки.",
            },
            {
                "question": "Как быстро стартуете?",
                "answer": "Подготовка и согласование занимают от 1 рабочего дня.",
            },
        ],
        "jsonld": json.dumps(jsonld_payload, ensure_ascii=False),
        "site_id": site.id,
    }


def _render_html_landing(site: Site, city: City, service: Service) -> str:
    city_pp = _city_prepositional(city.name)
    profile = build_keyword_profile(service.name, service.slug, city_pp, city.region)
    variants = _landing_variants(service.name, service.slug, city.name, city_pp, city.region)
    canonical_url = f"https://{settings.base_domain}/{city.slug}/{service.slug}/"
    page_title = _trim_for_title(f"{service.name} в {city_pp} | СЕО ГУРУ")
    meta_description = _trim_for_description(
        (
        f"{service.name} в {city_pp}. Бесплатный сайт при заказе SEO. "
        f"Работаем в {city_pp} и регионе {city.region} для русскоязычной аудитории."
        )
    )
    json_ld_payload = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness",
        "name": f"СЕО ГУРУ в {city_pp}",
        "url": canonical_url,
        "areaServed": city.name,
        "address": {"@type": "PostalAddress", "addressCountry": "RU", "addressLocality": city.name},
        "serviceType": service.name,
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.8",
            "reviewCount": "37",
            "bestRating": "5",
            "worstRating": "1",
        },
    }
    faq_json_ld_payload = {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [],
    }
    breadcrumb_json_ld_payload = {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": 1,
                "name": city_pp,
                "item": f"https://{settings.base_domain}/{city.slug}/",
            },
            {
                "@type": "ListItem",
                "position": 2,
                "name": service.name,
                "item": canonical_url,
            },
        ],
    }
    organization_json_ld_payload = {
        "@context": "https://schema.org",
        "@type": "Organization",
        "name": "СЕО ГУРУ",
        "url": f"https://{settings.base_domain}/",
        "logo": "https://tashev.ru/img/avatar.jpeg",
        "sameAs": ["https://t.me/tashev116"],
        "contactPoint": [
            {
                "@type": "ContactPoint",
                "contactType": "sales",
                "areaServed": "RU",
                "availableLanguage": ["ru"],
                "telephone": "+7-995-095-33-44",
            }
        ],
    }
    website_json_ld_payload = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": "СЕО ГУРУ",
        "url": f"https://{settings.base_domain}/",
        "inLanguage": "ru-RU",
    }
    service_json_ld_payload = {
        "@context": "https://schema.org",
        "@type": "Service",
        "name": f"{service.name} в {city_pp}",
        "serviceType": service.name,
        "areaServed": {"@type": "City", "name": city.name},
        "provider": {
            "@type": "Organization",
            "name": "СЕО ГУРУ",
            "url": f"https://{settings.base_domain}/",
        },
        "offers": {
            "@type": "Offer",
            "priceCurrency": "RUB",
            "price": "0",
            "priceValidUntil": "2027-12-31",
            "url": canonical_url,
            "description": "Сайт бесплатно при заказе SEO продвижения.",
        },
        "priceRange": "₽₽",
        "url": canonical_url,
        "aggregateRating": {
            "@type": "AggregateRating",
            "ratingValue": "4.8",
            "reviewCount": "37",
            "bestRating": "5",
            "worstRating": "1",
        },
        "review": [],
    }
    review_variant = _review_variant(city.slug, service.slug, city_pp, service.name)
    for faq_item in variants["faq_items"]:
        faq_json_ld_payload["mainEntity"].append(
            {
                "@type": "Question",
                "name": faq_item["question"],
                "acceptedAnswer": {"@type": "Answer", "text": faq_item["answer"]},
            }
        )
    service_json_ld_payload["review"] = [
        {
            "@type": "Review",
            "author": {"@type": "Person", "name": review_variant["author"]},
            "reviewRating": {"@type": "Rating", "ratingValue": review_variant["rating"], "bestRating": "5"},
            "reviewBody": review_variant["body"],
        }
    ]
    env = _template_env()
    tpl = env.get_template("landing_master.html.j2")
    return tpl.render(
        page_title=page_title,
        meta_description=meta_description,
        canonical_url=canonical_url,
        og_title=page_title,
        og_description=meta_description,
        og_url=canonical_url,
        og_image="https://tashev.ru/img/avatar.jpeg",
        robots_content=ROBOTS_INDEX if _page_is_open(city.slug, service.slug) else ROBOTS_NOINDEX,
        json_ld=json.dumps(json_ld_payload, ensure_ascii=False),
        faq_json_ld=json.dumps(faq_json_ld_payload, ensure_ascii=False),
        breadcrumb_json_ld=json.dumps(breadcrumb_json_ld_payload, ensure_ascii=False),
        organization_json_ld=json.dumps(organization_json_ld_payload, ensure_ascii=False),
        website_json_ld=json.dumps(website_json_ld_payload, ensure_ascii=False),
        service_json_ld=json.dumps(service_json_ld_payload, ensure_ascii=False),
        yandex_metrika_id=settings.yandex_metrika_id,
        yandex_metrika_goal_submit=settings.yandex_metrika_goal_submit,
        city=city.name,
        city_slug=city.slug,
        city_pp=city_pp,
        region=city.region,
        unique_offer_line=f"Экономия до 150 000₽ для бизнеса в {city_pp}.",
        founder_photo="https://tashev.ru/img/avatar.jpeg",
        keyword=service.name,
        hero_line_1=profile["hero_line_1"],
        hero_line_2=profile["hero_line_2"],
        hero_line_3=profile["hero_line_3"],
        hero_subtitle=profile["hero_subtitle"],
        secondary_cta_text=profile["secondary_cta_text"],
        offer_description=profile["offer_description"],
        services_subtitle=profile["services_subtitle"],
        pricing_title=profile["pricing_title"],
        pricing_description=profile["pricing_description"],
        founder_focus_line=profile["founder_focus_line"],
        contact_subtitle=profile["contact_subtitle"],
        footer_blurb=profile["footer_blurb"],
        audience_cards=variants["audience_cards"],
        deliverables=variants["deliverables"],
        review_cards=variants["review_cards"],
        proof_points=variants["proof_points"],
        faq_items=variants["faq_items"],
        counter_1_target=profile["counter_1_target"],
        counter_1_label=profile["counter_1_label"],
        counter_2_target=profile["counter_2_target"],
        counter_2_label=profile["counter_2_label"],
        counter_3_target=profile["counter_3_target"],
        counter_3_label=profile["counter_3_label"],
        counter_4_target=profile["counter_4_target"],
        counter_4_label=profile["counter_4_label"],
    )


def _render_city_context(city: City, services: list[Service]) -> dict:
    city_pp = _city_prepositional(city.name)
    title = f"Услуги в {city_pp} - каталог на {settings.base_domain}"
    description = f"Подбор услуг в {city_pp} для русскоязычной аудитории."
    canonical = f"https://{settings.base_domain}/{city.slug}/"
    jsonld_payload = {
        "@context": "https://schema.org",
        "@type": "Place",
        "name": city.name,
        "address": {"addressCountry": "RU", "addressLocality": city.name},
    }
    return {
        "title": title,
        "description": description,
        "canonical": canonical,
        "og_title": title,
        "og_description": description,
        "city_name": city.name,
        "city_slug": city.slug,
        "h1": f"Услуги в {city_pp}",
        "body_intro": f"Сводная страница услуг в {city_pp}.",
        "services": [{"name": service.name, "slug": service.slug} for service in services],
        "jsonld": json.dumps(jsonld_payload, ensure_ascii=False),
    }


def _content_hash(context: dict) -> str:
    serialized = json.dumps(context, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def render_sites_to_hugo(
    db: Session,
    city_limit: int | None = None,
    service_limit: int | None = None,
    site_ids: list[int] | None = None,
) -> dict[str, int]:
    site_stmt = select(Site).order_by(Site.id)
    if site_ids:
        site_stmt = site_stmt.where(Site.id.in_(site_ids))
    sites = db.scalars(site_stmt).all()

    env = _template_env()
    site_template = env.get_template("site_page.md.j2")
    city_template = env.get_template("city_page.md.j2")

    rendered_pages = 0
    skipped_pages = 0
    city_pages_rendered = 0
    city_pages_skipped = 0

    if city_limit is not None:
        allowed_city_ids = set(db.scalars(select(City.id).order_by(City.id).limit(city_limit)).all())
        sites = [site for site in sites if site.city_id in allowed_city_ids]

    if service_limit is not None:
        allowed_service_ids = set(db.scalars(select(Service.id).order_by(Service.id).limit(service_limit)).all())
        sites = [site for site in sites if site.service_id in allowed_service_ids]

    city_cache = {city.id: city for city in db.scalars(select(City).where(City.id.in_({site.city_id for site in sites}))).all()}
    service_cache = {
        service.id: service for service in db.scalars(select(Service).where(Service.id.in_({site.service_id for site in sites}))).all()
    }
    city_services: dict[int, dict[int, Service]] = {}

    for site in sites:
        city = city_cache.get(site.city_id)
        service = service_cache.get(site.service_id)
        if city is None or service is None:
            continue

        city_services.setdefault(city.id, {})[service.id] = service

        context = _render_context(site, city, service)
        content_hash = _content_hash(context)
        page_path = f"{city.slug}/{service.slug}/index.md"
        html_preview = _render_html_landing(site, city, service)
        preview_dir = Path(settings.releases_root) / str(site.id) / "manual-build" / "public" / city.slug / service.slug
        preview_dir.mkdir(parents=True, exist_ok=True)
        (preview_dir / "index.html").write_text(html_preview, encoding="utf-8")
        preview_root = Path(settings.releases_root) / str(site.id) / "manual-build" / "public"
        _write_preview_seo_files(
            preview_root,
            urls=[
                f"https://{settings.base_domain}/{city.slug}/",
                f"https://{settings.base_domain}/{city.slug}/{service.slug}/",
            ],
        )
        assets_dir = Path(settings.releases_root) / str(site.id) / "manual-build" / "public" / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        source_photo = Path("app/assets/founder-rinat.jpg")
        if source_photo.exists():
            shutil.copy2(source_photo, assets_dir / "founder-rinat.jpg")

        existing_page = db.scalar(
            select(SitePage).where(SitePage.site_id == site.id, SitePage.path == page_path)
        )

        if existing_page and existing_page.content_hash == content_hash:
            skipped_pages += 1
            continue

        output_dir = Path(settings.hugo_source_dir) / "content" / city.slug / service.slug
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "index.md"
        output_file.write_text(site_template.render(**context), encoding="utf-8")

        if existing_page is None:
            existing_page = SitePage(
                site_id=site.id,
                path=page_path,
                content_hash=content_hash,
                last_generated_at=datetime.now(timezone.utc),
            )
            db.add(existing_page)
        else:
            existing_page.content_hash = content_hash
            existing_page.last_generated_at = datetime.now(timezone.utc)

        rendered_pages += 1

    for city_id, service_map in city_services.items():
        city = city_cache.get(city_id)
        if city is None:
            continue

        representative_site_id = next((site.id for site in sites if site.city_id == city_id), None)
        if representative_site_id is None:
            continue

        context = _render_city_context(city, list(service_map.values()))
        content_hash = _content_hash(context)
        page_path = f"{city.slug}/index.md"
        preview_city_dir = Path(settings.releases_root) / str(representative_site_id) / "manual-build" / "public" / city.slug
        preview_city_dir.mkdir(parents=True, exist_ok=True)
        preview_city_html = _render_city_hub_html(city, list(service_map.values()))
        (preview_city_dir / "index.html").write_text(preview_city_html, encoding="utf-8")

        existing_page = db.scalar(
            select(SitePage).where(SitePage.site_id == representative_site_id, SitePage.path == page_path)
        )
        if existing_page and existing_page.content_hash == content_hash:
            city_pages_skipped += 1
            continue

        output_dir = Path(settings.hugo_source_dir) / "content" / city.slug
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / "index.md"
        output_file.write_text(city_template.render(**context), encoding="utf-8")

        if existing_page is None:
            existing_page = SitePage(
                site_id=representative_site_id,
                path=page_path,
                content_hash=content_hash,
                last_generated_at=datetime.now(timezone.utc),
            )
            db.add(existing_page)
        else:
            existing_page.content_hash = content_hash
            existing_page.last_generated_at = datetime.now(timezone.utc)

        city_pages_rendered += 1

    db.commit()
    generate_sitemaps(db)
    return {
        "processed_sites": len(sites),
        "rendered_pages": rendered_pages + city_pages_rendered,
        "skipped_pages": skipped_pages + city_pages_skipped,
    }
