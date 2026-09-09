"""Russian city-name morphology used by x-gu.ru rendering and repair tools.

This is intentionally dependency-free. Exact overrides cover names that are
ambiguous or do not follow the conservative rules below. Add an override when
a production city is known to require a special form; do not fork morphology
logic into another script.
"""
from __future__ import annotations


CITY_PREPOSITIONAL_OVERRIDES: dict[str, str] = {
    "Москва": "Москве",
    "Санкт-Петербург": "Санкт-Петербурге",
    "Нижний Новгород": "Нижнем Новгороде",
    "Великий Новгород": "Великом Новгороде",
    "Нижний Тагил": "Нижнем Тагиле",
    "Старый Оскол": "Старом Осколе",
    "Новый Уренгой": "Новом Уренгое",
    "Сергиев Посад": "Сергиевом Посаде",
    "Орёл": "Орле",
    "Орел": "Орле",
    "Йошкар-Ола": "Йошкар-Оле",
    "Набережные Челны": "Набережных Челнах",
    "Минеральные Воды": "Минеральных Водах",
    "Великие Луки": "Великих Луках",
    "Химки": "Химках",
    "Мытищи": "Мытищах",
    "Березники": "Березниках",
    "Ессентуки": "Ессентуках",
    "Ярославль": "Ярославле",
    "Севастополь": "Севастополе",
    "Ставрополь": "Ставрополе",
    "Петропавловск-Камчатский": "Петропавловске-Камчатском",
    "Каменск-Уральский": "Каменске-Уральском",
    "Гусь-Хрустальный": "Гусь-Хрустальном",
}


def _decline_adjective(word: str) -> str | None:
    lower = word.lower()
    if lower.endswith("ый") or lower.endswith("ой"):
        return word[:-2] + "ом"
    if lower.endswith("ий"):
        return word[:-2] + "ем"
    if lower.endswith("ая"):
        return word[:-2] + "ой"
    if lower.endswith("яя"):
        return word[:-2] + "ей"
    if lower.endswith("ые"):
        return word[:-2] + "ых"
    if lower.endswith("ие"):
        return word[:-2] + "их"
    return None


def _decline_simple(name: str) -> str:
    lower = name.lower()
    adjective = _decline_adjective(name)
    if adjective is not None:
        return adjective
    if lower.endswith("ия"):
        return name[:-2] + "ии"
    if lower.endswith("а"):
        return name[:-1] + "е"
    if lower.endswith("я"):
        return name[:-1] + "е"
    if lower.endswith("ь"):
        return name[:-1] + "и"
    if lower.endswith("ы"):
        return name[:-1] + "ах"
    # Many city names ending in -и are indeclinable (for example, Сочи), so
    # plural -и names are handled through exact overrides instead of guessing.
    if lower.endswith(("о", "е", "и", "у", "ю", "э", "ё")):
        return name
    return name + "е"


def city_prepositional(city_name: str) -> str:
    """Return a conservative Russian prepositional form for a city name."""
    city_name = " ".join(city_name.strip().split())
    if not city_name:
        return city_name
    if city_name in CITY_PREPOSITIONAL_OVERRIDES:
        return CITY_PREPOSITIONAL_OVERRIDES[city_name]

    # Preserve fixed second parts in names such as Ростов-на-Дону and
    # Комсомольск-на-Амуре while declining the first part.
    for sep in ("-на-", "-над-", "-под-"):
        if sep in city_name:
            head, _, tail = city_name.partition(sep)
            return city_prepositional(head) + sep + tail

    words = city_name.split(" ")
    if len(words) == 2:
        first_adj = _decline_adjective(words[0])
        if first_adj is not None:
            return f"{first_adj} {_decline_simple(words[1])}"

    # Productive pattern for names like Каменск-Уральский. Exact exceptions
    # above take precedence for compounds whose first part must not decline.
    if city_name.count("-") == 1:
        first, second = city_name.split("-", 1)
        second_adj = _decline_adjective(second)
        if second_adj is not None:
            return f"{_decline_simple(first)}-{second_adj}"

    return _decline_simple(city_name)
