import unittest

from calculations import calculate_layout, calculate_totals


class LayoutTests(unittest.TestCase):
    def test_chooses_rotated_layout_when_better(self):
        layout = calculate_layout(1000, 600, 600, 250, 5, True)
        self.assertEqual(layout.orientation, "Поворот 90°")
        self.assertEqual(layout.pieces_per_sheet, 4)
        self.assertEqual(layout.sheets, 2)

    def test_rejects_oversized_part(self):
        with self.assertRaises(ValueError):
            calculate_layout(100, 100, 101, 50, 1, True)

    def test_cut_lines_use_only_occupied_grid(self):
        layout = calculate_layout(2800, 2070, 600, 400, 10, False)
        self.assertEqual(layout.columns, 4)
        self.assertEqual(layout.rows, 5)
        self.assertEqual(layout.cut_lines, 5)

    def test_totals(self):
        totals = calculate_totals(
            1000,
            2,
            50,
            3,
            100,
            [{"price": 25, "quantity": 4}],
        )
        self.assertEqual(totals["material"], 2000)
        self.assertEqual(totals["cutting"], 250)
        self.assertEqual(totals["extras"], 100)
        self.assertEqual(totals["grand"], 2350)


if __name__ == "__main__":
    unittest.main()
