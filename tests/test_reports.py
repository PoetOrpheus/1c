import unittest

from reports import document_html


def sample_order():
    return {
        "number": "ЗК-20261012-0001",
        "date": "12.10.2026",
        "customer": "ООО «Тест»",
        "material": {
            "name": "ЛДСП <Белый>",
            "thickness": 16,
            "width": 2800,
            "height": 2070,
            "price_sheet": 3850,
        },
        "part": {"width": 600, "height": 400, "quantity": 10},
        "layout": {
            "orientation": "Без поворота",
            "columns": 4,
            "rows": 5,
            "pieces_per_sheet": 20,
            "sheets": 1,
            "utilization_pct": 41.41,
            "waste_pct": 58.59,
        },
        "cutting": {
            "name": "Прямолинейный распил",
            "count": 5,
            "price_per_cut": 80,
            "setup_fee": 300,
        },
        "extras": [
            {"name": "Упаковка", "quantity": 1, "unit": "шт.", "sum": 250}
        ],
        "totals": {"material": 3850, "cutting": 700, "extras": 250, "grand": 4800},
    }


class ReportTests(unittest.TestCase):
    def test_report_uses_structured_layout(self):
        html = document_html(sample_order(), "report")
        self.assertIn('class="info-grid"', html)
        self.assertIn('class="grand"', html)
        self.assertNotIn("<pre>", html)
        self.assertIn("ЛДСП &lt;Белый&gt;", html)

    def test_receipt_uses_aligned_table(self):
        html = document_html(sample_order(), "receipt")
        self.assertIn('class="page receipt"', html)
        self.assertIn('class="amount"', html)
        self.assertIn("Документ не является кассовым чеком", html)


if __name__ == "__main__":
    unittest.main()
