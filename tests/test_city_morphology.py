from __future__ import annotations

import unittest

from city_morphology import city_prepositional


class CityPrepositionalTests(unittest.TestCase):
    def test_major_and_irregular_cities(self) -> None:
        cases = {
            "Москва": "Москве",
            "Санкт-Петербург": "Санкт-Петербурге",
            "Нижний Новгород": "Нижнем Новгороде",
            "Великий Новгород": "Великом Новгороде",
            "Нижний Тагил": "Нижнем Тагиле",
            "Старый Оскол": "Старом Осколе",
            "Новый Уренгой": "Новом Уренгое",
            "Йошкар-Ола": "Йошкар-Оле",
            "Набережные Челны": "Набережных Челнах",
            "Минеральные Воды": "Минеральных Водах",
            "Химки": "Химках",
            "Мытищи": "Мытищах",
            "Березники": "Березниках",
            "Ессентуки": "Ессентуках",
            "Петропавловск-Камчатский": "Петропавловске-Камчатском",
            "Каменск-Уральский": "Каменске-Уральском",
            "Гусь-Хрустальный": "Гусь-Хрустальном",
        }
        for source, expected in cases.items():
            with self.subTest(city=source):
                self.assertEqual(city_prepositional(source), expected)

    def test_productive_rules(self) -> None:
        cases = {
            "Казань": "Казани",
            "Самара": "Самаре",
            "Краснодар": "Краснодаре",
            "Пермь": "Перми",
            "Сочи": "Сочи",
            "Ростов-на-Дону": "Ростове-на-Дону",
            "Комсомольск-на-Амуре": "Комсомольске-на-Амуре",
        }
        for source, expected in cases.items():
            with self.subTest(city=source):
                self.assertEqual(city_prepositional(source), expected)

    def test_normalizes_whitespace(self) -> None:
        self.assertEqual(city_prepositional("  Нижний   Новгород  "), "Нижнем Новгороде")


if __name__ == "__main__":
    unittest.main()
