import tempfile
import unittest
from pathlib import Path

from database import Database
from production import (new_order, new_node, default_stages, flatten_nodes, move_node,
                        copy_branch, calculate_production, numeric, transfer_laser_matches,
                        new_auxiliary_stage, prepare_order_stages)
from reports import document_html


def fixture():
    order = new_order("ТЕСТ-001")
    rack = new_node("assembly")
    rack.update(name="Стеллаж", quantity=2)
    frame = new_node("assembly", rack["id"])
    frame.update(name="Секция", quantity=3)
    part = new_node("part", frame["id"])
    part.update(name="Полка <А>", quantity=4, width=1000, height=500, bends=2, mass=2,
                laser_minutes=1.5, material=dict(id=1, name="Сталь", thickness=1,
                                               width=2000, height=1000, price_sheet=1000))
    order["nodes"] = [rack, frame, part]
    return order


class ProductionTests(unittest.TestCase):
    def test_reduced_stages_keep_original_ids_and_laser(self):
        stages = default_stages()
        self.assertEqual([s['id'] for s in stages],
                         [f'stage_{n}' for n in (1, 2, 4, 10, 16, 19, 22, 25, 26)])
        self.assertEqual(stages[2]['kind'], 'laser')
        self.assertEqual(stages[1]["description"], "Закупка")

    def test_removed_stages_archived_without_changing_original_order(self):
        order = fixture()
        old = new_auxiliary_stage()
        old.update(id='stage_27', name='Старая операция')
        old['settings']['fixed_cost'] = 550
        order['stages'].append(old)
        updated = prepare_order_stages(order)
        self.assertEqual(len(order['stages']), 10)
        self.assertEqual(len(updated['stages']), 9)
        self.assertEqual(updated['removed_stages'], [old])
        self.assertEqual(prepare_order_stages(updated), updated)
        self.assertEqual(calculate_production(order)['totals']['grand'] -
                         calculate_production(updated)['totals']['grand'], 550)

    def test_custom_auxiliary_cost_is_added_once_and_survives_reload(self):
        order = fixture()
        base = calculate_production(order)['totals']['grand']
        stage = new_auxiliary_stage()
        stage['name'] = 'Доработка'
        stage['settings']['fixed_cost'] = 1234.5
        order['stages'].append(stage)
        result = calculate_production(prepare_order_stages(order))
        self.assertEqual(result['totals']['grand'], base + 1234.5)
        self.assertEqual(stage['title'], 'Вспомогательный')
        self.assertNotEqual(stage['id'], new_auxiliary_stage()['id'])
        self.assertIn('Доработка', document_html(result, 'report'))
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / 'orders.db')
            key = db.save_order(result)
            restored = prepare_order_stages(db.get_order(key))
            self.assertEqual(restored['stages'][-1], stage)
            self.assertEqual(calculate_production(restored)['totals']['grand'], base + 1234.5)
            db.close()

    def test_nested_quantities_and_material(self):
        result = calculate_production(fixture())
        self.assertEqual(result["calculation"]["quantity"], 24)
        self.assertEqual(result["calculation"]["bends"], 48)
        self.assertEqual(result["calculation"]["paint_area"], 24)
        self.assertEqual(result["totals"]["material"], 6000)

    def test_prevents_cycles_and_missing_parent(self):
        order = fixture()
        root, child, part = order["nodes"]
        with self.assertRaises(ValueError):
            move_node(order["nodes"], root["id"], child["id"])
        root["parent_id"] = child["id"]
        with self.assertRaises(ValueError):
            flatten_nodes(order["nodes"])
        root["parent_id"] = "missing"
        with self.assertRaises(ValueError):
            flatten_nodes(order["nodes"])

    def test_copy_branch_remaps_children_and_quantities(self):
        order = fixture()
        new_id = copy_branch(order["nodes"], order["nodes"][0]["id"])
        self.assertNotEqual(new_id, order["nodes"][0]["id"])
        self.assertEqual(calculate_production(order)["calculation"]["quantity"], 48)

    def test_disabled_stages_and_otk(self):
        order = fixture()
        stage = next(s for s in order["stages"] if s["kind"] == "bending")
        stage["settings"].update(per_bend=2, setup=4, rate=600, otk_minutes=10)
        stage["otk"] = True
        result = calculate_production(order)
        row = next(s for s in result["calculation"]["stages"] if s["id"] == stage["id"])
        self.assertEqual((row["minutes"], row["cost"]), (110, 1100))
        stage["enabled"] = False
        row = next(s for s in calculate_production(order)["calculation"]["stages"] if s["id"] == stage["id"])
        self.assertEqual((row["minutes"], row["cost"]), (0, 0))

    def test_painting_consumables_and_cycles(self):
        order = fixture()
        stage = next(s for s in order["stages"] if s["kind"] == "painting")
        stage["settings"].update(oven_area=10, cycle_minutes=20, rate=60, paint_price=100, primer_per_m2=.1, primer_price=50)
        result = calculate_production(order)
        row = next(s for s in result["calculation"]["stages"] if s["id"] == stage["id"])
        self.assertEqual(row["details"]["Количество загрузок печи"], 3)
        self.assertEqual(row["minutes"], 216)  # 24*2*2 + 3*20 + 60*100%.
        self.assertEqual(row["cost"], 768)  # 216 labour + 432 paint + 120 primer.

    def test_laser_rows_replace_material_estimate_without_double_counting(self):
        order = fixture()
        order["laser_rows"] = [dict(material="Сталь", characteristic="2×1", center="Лазер", thickness=1,
                                    coef=1, quantity=2, waste_pct=20, cut_minutes=10, sheets=2, weight=5,
                                    extra_minutes=1, customer_material=False, price=1000)]
        result = calculate_production(order)
        self.assertEqual(result["totals"]["material"], 2000)
        self.assertEqual(result["calculation"]["laser"]["total"], 18)  # 10+4+3+1
        order["laser_rows"][0]["customer_material"] = True
        self.assertEqual(calculate_production(order)["totals"]["material"], 0)

    def test_time_for_packing_is_whole_order(self):
        order = fixture()
        packing = next(s for s in order["stages"] if s["kind"] == "packing")
        packing["settings"].update(minutes=10, rate=120)
        row = next(s for s in calculate_production(order)["calculation"]["stages"] if s["kind"] == "packing")
        self.assertEqual((row["minutes"], row["cost"]), (10, 20))

    def test_laser_transfer_is_idempotent_and_respects_flags(self):
        order = fixture()
        part = order["nodes"][-1]
        order["laser_unmatched"] = [dict(node_id=part["id"], quantity=2, minutes=10, area=1, mass=3),
                                     dict(node_id=part["id"], quantity=3, minutes=20, area=1, mass=3)]
        self.assertEqual(transfer_laser_matches(order), 1)
        self.assertEqual(part["laser_minutes"], 1.5)  # Existing value is protected.
        order["laser_include_filled"] = True
        transfer_laser_matches(order)
        self.assertEqual((part["laser_minutes"], part["area"], part["mass"]), (6, 1, 3))
        transfer_laser_matches(order)
        self.assertEqual(part["laser_minutes"], 6)
        order["laser_area_only"] = True
        order["laser_unmatched"][0]["minutes"] = 999
        transfer_laser_matches(order)
        self.assertEqual(part["laser_minutes"], 6)

    def test_rejects_invalid_numbers_and_oven(self):
        for value in ("nan", "inf", "-1"):
            with self.assertRaises(ValueError):
                numeric(value)
        order = fixture()
        next(s for s in order["stages"] if s["kind"] == "painting")["settings"]["oven_area"] = 0
        with self.assertRaises(ValueError):
            calculate_production(order)

    def test_database_roundtrip_and_historical_prices(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Database(Path(directory) / "orders.db")
            order = calculate_production(fixture())
            key = db.save_order(order)
            db.set_state("production_draft", {"order": order, "saved_id": key})
            self.assertEqual(db.get_order(key), order)
            self.assertEqual(db.get_state("production_draft")["order"], order)
            order["customer"] = "Новый заказчик"
            db.update_order(key, order)
            self.assertEqual(db.get_order(key)["customer"], "Новый заказчик")
            self.assertEqual(len(db.list_orders()), 1)
            db.close()

    def test_documents_contain_tree_stages_and_escaped_names(self):
        order = calculate_production(fixture())
        report = document_html(order, "report")
        receipt = document_html(order, "receipt")
        self.assertIn("Полка &lt;А&gt;", report)
        self.assertIn("Стеллаж", report)
        self.assertIn("Сумма продажная с НДС", report)
        self.assertIn("Покрытие", receipt)
        self.assertNotIn("<А>", report)


if __name__ == "__main__":
    unittest.main()
