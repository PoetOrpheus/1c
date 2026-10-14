from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from cam_import import (read_batch, read_document, aggregate_parts, apply_import, duration, default_mapping,
                        parse_bit_pdf, digest)
from database import Database
from production import new_order, new_node, calculate_production, transfer_laser_matches


FIXTURES = Path(__file__).resolve().parents[1] / "1c_файлы_мишы"


@unittest.skipUnless(FIXTURES.is_dir(), "Нет исходных файлов заказчика")
class CAMImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.batch = read_batch(FIXTURES.iterdir())
        cls.by_name = {d["filename"]: d for d in cls.batch["documents"]}

    def test_all_supplied_files_parse_without_errors_and_duplicate_pairs_collapse(self):
        self.assertEqual(self.batch["errors"], [])
        self.assertEqual(len(self.batch["documents"]), 9)
        self.assertEqual(len(self.batch["duplicates"]), 4)
        parts = aggregate_parts(self.batch["documents"])
        self.assertEqual(len(parts), 41)
        self.assertEqual(sum(p["quantity"] for p in parts.values()), 152)

    def test_pdf_and_excel_have_identical_semantics(self):
        for stem in ("08ps0_8", "08ps1_2", "08ps2", "08ps3"):
            excel = self.by_name[stem + ".xlsx"]
            pdf = read_document(FIXTURES / (stem + ".pdf"))
            self.assertEqual(excel["fingerprint"], pdf["fingerprint"])
            self.assertEqual(excel["parts"], pdf["parts"])

    def test_bit_counts_units_and_mass_discrepancy(self):
        d = self.by_name["08ps0_8.xlsx"]
        self.assertEqual(d["stock_count"], 1)
        self.assertEqual(d["thickness"], .8)
        self.assertAlmostEqual(d["cut_minutes"], 5 + 32 / 60)
        p = d["parts"][0]
        self.assertEqual(p["quantity"], 2)  # Not the kg column.
        self.assertEqual(p["mass"], 18.3002)
        self.assertAlmostEqual(p["flat_area"], .22875250)
        self.assertEqual(d["total_part_mass"], 11.3235)
        self.assertIsNone(p["minutes"])
        self.assertTrue(any("Масса в исходнике не сходится" in w for w in d["warnings"]))

    def test_trutops_encoding_geometry_and_per_part_time(self):
        d = self.by_name["08ps1.5_1.HTML"]
        self.assertAlmostEqual(d["cut_minutes"], 5 + 13 / 60)
        self.assertEqual(d["stock_mass"], 52.99)
        self.assertEqual(d["thickness"], 1.5)
        p = d["parts"][0]
        self.assertEqual(p["name"], "41240.60.002 Ребро")
        self.assertEqual(p["quantity"], 24)
        self.assertEqual(p["minutes"], .02)
        self.assertAlmostEqual(p["flat_area"], .00146461)

    def test_sekirus_does_not_double_count_or_guess_radius_as_thickness(self):
        d = self.by_name["Sekirus.pdf"]
        self.assertEqual([p["quantity"] for p in d["parts"]], [4, 4])
        self.assertEqual(d["stock_count"], 1)
        self.assertEqual(d["raw_summary"]["available_tubes"], 999)
        self.assertEqual(d["actual_size"][0], 4271.96)
        self.assertIsNone(d["thickness"])
        self.assertIsNone(d["cut_minutes"])
        self.assertTrue(all(p["mass"] is None and p["flat_area"] is None for p in d["parts"]))

    def test_full_import_counts_and_idempotence(self):
        source = new_order("TEST")
        order, stats = apply_import(source, self.batch["documents"])
        self.assertFalse(source["nodes"])
        self.assertEqual(stats["created"], 41)
        self.assertEqual(len(order["laser_rows"]), 9)
        self.assertEqual(len(order["laser_unmatched"]), 45)
        c = calculate_production(order)["calculation"]
        self.assertEqual(c["quantity"], 152)
        self.assertAlmostEqual(c["laser"]["cutting"], 18.25)
        again, stats = apply_import(order, self.batch["documents"])
        self.assertEqual(stats["imported"], 0)
        self.assertEqual(order, again)

    def test_conflicting_mass_skipped_unless_explicitly_accepted(self):
        d = self.by_name["08ps0_8.xlsx"]
        order, _ = apply_import(new_order("TEST"), [d])
        self.assertTrue(all(n["mass"] == 0 for n in order["nodes"] if n["kind"] == "part"))
        self.assertEqual(order["cam_imports"][0]["parts"][0]["source_mass"], 18.3002)
        self.assertEqual(d["parts"][0]["mass"], 18.3002)  # Source preview unchanged.
        accepted, _ = apply_import(new_order("TEST"), [d], accept_conflicting_mass=True)
        self.assertAlmostEqual(calculate_production(accepted)["calculation"]["mass"], 141.5445)

    def test_blank_fraction_and_whole_sheet_price(self):
        d = self.by_name["08ps0_8.xlsx"]
        order, _ = apply_import(new_order("TEST"), [d], configs={d["key"]: dict(basis="blank", price=1000)})
        self.assertAlmostEqual(order["laser_rows"][0]["quantity"], 1753.97 / 2500)
        self.assertEqual(calculate_production(order)["totals"]["material"], 701.59)
        self.assertEqual(order["laser_rows"][0]["sheets"], 1)

    def test_missing_values_never_erase_existing_values_on_transfer(self):
        d = self.by_name["Sekirus.pdf"]
        order, _ = apply_import(new_order("TEST"), [d])
        node = next(n for n in order["nodes"] if n["kind"] == "part")
        node.update(mass=2, area=3, laser_minutes=4)
        order["laser_include_filled"] = True
        transfer_laser_matches(order)
        self.assertEqual((node["mass"], node["area"], node["laser_minutes"]), (2, 3, 4))

    def test_existing_counts_preserved_and_ambiguous_name_not_guessed(self):
        d = self.by_name["08ps1.5_3.HTML"]
        source = new_order("TEST")
        node = new_node()
        node.update(name=d["parts"][0]["name"], code=d["parts"][0]["code"], thickness=1.5, quantity=10)
        source["nodes"] = [node]
        order, _ = apply_import(source, [d])
        self.assertEqual(order["nodes"][0]["quantity"], 10)
        source["nodes"].append(dict(deepcopy(node), id="second"))
        self.assertEqual(default_mapping(source, d["parts"][0]), "unmatched")

    def test_parts_from_different_files_merge_but_counts_do_not_double(self):
        first = self.by_name["08ps1.5_1.HTML"]
        second = self.by_name["08ps1.5_2.HTML"]
        order, _ = apply_import(new_order("TEST"), [first])
        order, _ = apply_import(order, [second])
        rib = next(n for n in order["nodes"] if n["code"] == "41240.30.004")
        self.assertEqual(rib["quantity"], 8)  # 2 + 6 from different programs.
        order, _ = apply_import(order, [second], replace=True)
        rib = next(n for n in order["nodes"] if n["code"] == "41240.30.004")
        self.assertEqual(rib["quantity"], 8)

    def test_parent_multiplicity_and_rollback(self):
        d = self.by_name["08ps1.5_3.HTML"]  # Two copies of each part.
        source = new_order("TEST")
        parent = new_node("assembly")
        parent["quantity"] = 2
        source["nodes"] = [parent]
        order, _ = apply_import(source, [d], parent_id=parent["id"])
        self.assertEqual([n["quantity"] for n in order["nodes"] if n["kind"] == "part"], [1, 1])
        parent["quantity"] = 3
        before = deepcopy(source)
        with self.assertRaises(ValueError):
            apply_import(source, [d], parent_id=parent["id"])
        self.assertEqual(source, before)

    def test_area_only_does_not_create_nests_or_change_time_count(self):
        d = self.by_name["08ps1.5_3.HTML"]
        order, _ = apply_import(new_order("TEST"), [d])
        node = next(n for n in order["nodes"] if n["kind"] == "part")
        node.update(quantity=20, laser_minutes=10, area=0)
        updated, _ = apply_import(order, [d], area_only=True, paint_sides=1)
        updated_node = next(n for n in updated["nodes"] if n["id"] == node["id"])
        self.assertEqual(updated_node["laser_minutes"], 10)
        self.assertEqual(updated_node["quantity"], 20)
        self.assertEqual(updated["laser_rows"], order["laser_rows"])
        self.assertAlmostEqual(updated_node["area"], .40502669)

    def test_unreadable_file_does_not_discard_valid_files(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "broken.pdf"
            path.write_bytes(b"broken")
            batch = read_batch([path, FIXTURES / "08ps1.5_3.HTML"])
            self.assertEqual(len(batch["errors"]), 1)
            self.assertEqual(len(batch["documents"]), 1)

    def test_sqlite_roundtrip_preserves_import_provenance(self):
        order, _ = apply_import(new_order("TEST"), self.batch["documents"])
        order = calculate_production(order)
        with tempfile.TemporaryDirectory() as temp:
            db = Database(Path(temp) / "test.db")
            key = db.save_order(order)
            loaded = db.get_order(key)
            self.assertEqual(calculate_production(loaded)["totals"], order["totals"])
            self.assertEqual(loaded["cam_imports"][0]["fingerprint"], order["cam_imports"][0]["fingerprint"])
            self.assertEqual(len(loaded["nodes"]), len(order["nodes"]))
            db.close()


class CAMValueTests(unittest.TestCase):
    def test_time_units(self):
        self.assertEqual(duration("1 : 02 : 30 [h:min:s]"), 62.5)
        with self.assertRaises(ValueError):
            duration("0:90:00")


if __name__ == "__main__":
    unittest.main()
