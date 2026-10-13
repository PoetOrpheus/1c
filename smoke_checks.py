"""GUI smoke checks used by the portable application's --self-test switch."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk

from production import new_order, new_node, calculate_production


def sample_specification(db):
    order = new_order(db.next_order_number())
    order.update(customer="Тестовый заказчик", name="Стеллаж металлический")
    rack = new_node("assembly")
    rack.update(name="Стеллаж", quantity=2, code="СТ-001")
    material = db.list_records("materials", active_only=True)[0]
    shelf = new_node("part", rack["id"])
    shelf.update(name="Полка", quantity=4, code="ПЛ-001", width=600, height=400,
                 bends=4, mass=2.2, laser_minutes=1.5, material=material)
    post = new_node("part", rack["id"])
    post.update(name="Стойка", quantity=4, code="СТ-002", width=1200, height=100,
                bends=2, mass=1.7, laser_minutes=2, material=material)
    order["nodes"] = [rack, shelf, post]
    for stage in order["stages"]:
        stage["settings"]["rate"] = 900
    next(s for s in order["stages"] if s["kind"] == "filing")["settings"]["per_part"] = 1
    next(s for s in order["stages"] if s["kind"] == "painting")["settings"].update(cycle_minutes=15, paint_price=400)
    next(s for s in order["stages"] if s["kind"] == "packing")["settings"]["minutes"] = 10
    return calculate_production(order)


def run_gui_checks(output_dir):
    import app as app_module
    from app import Application, DocumentPreview
    from production_ui import FormDialog
    from reports import document_html
    previous_data = app_module.DATA_DIR
    application = None
    with TemporaryDirectory(prefix="raskroy-self-test-", dir=output_dir) as temp:
        app_module.DATA_DIR = Path(temp) / "data"
        try:
            application = Application()
            errors = []
            application.report_callback_exception = lambda kind, error, traceback: errors.append(repr(error))
            application.update()
            tab = application.order_tab
            order = sample_specification(application.db)
            tab.load_order(order, None)
            application.update()
            assert len(tab.stages_tree.get_children()) == 34
            assert len(tab.tabs) == 10
            assert tab.calculate()["calculation"]["quantity"] == 16

            # Exercise the real part form and save callback.
            root = tab.order["nodes"][0]
            tab.tree.selection_set(root["id"])
            tab.edit_node()
            dialog = next(w for w in tab.winfo_children() if isinstance(w, FormDialog))
            dialog.variables["name"].set("Крепление")
            dialog.variables["quantity"].set("3")
            dialog.variables["width"].set("50")
            dialog.variables["height"].set("80")
            dialog.save()
            assert len(tab.order["nodes"]) == 4
            assert tab.order["calculation"]["quantity"] == 22

            # Enter invalid then valid norm; invalid values must not enter the draft.
            bending = next(s for s in tab.order["stages"] if s["kind"] == "bending")
            var = tab.setting_vars[bending["id"]]["per_bend"]
            var.set("ошибка")
            assert not tab.flush()
            var.set("2,5")
            assert tab.flush()
            before = tab.order["totals"]["grand"]
            tab._toggle(bending["id"], "enabled")
            assert tab.order["totals"]["grand"] < before
            tab._toggle(bending["id"], "enabled")

            # Enter a laser row through its form.
            tab.edit_laser()
            dialog = next(w for w in tab.winfo_children() if isinstance(w, FormDialog))
            dialog.variables["material"].set("Сталь 08ПС")
            dialog.variables["quantity"].set("0,7")
            dialog.variables["price"].set("1000")
            dialog.variables["cut_minutes"].set("6,6")
            dialog.save()
            assert tab.order["totals"]["material"] == 700
            assert len(tab.laser_tree.get_children()) == 1

            tab.save_order()
            assert len(application.db.list_orders()) == 1
            saved_id = tab.saved_id
            tab.customer_var.set("Изменённый заказчик")
            tab.save_order()
            saved = application.db.get_order(saved_id)
            assert saved["customer"] == "Изменённый заказчик"
            assert len(application.db.list_orders()) == 1
            assert application.db.get_state("production_draft")["order"] == tab.order
            tab.load_order(deepcopy(saved), saved_id)
            assert tab.order["totals"] == saved["totals"]
            for page in tab.tabs.values():
                tab.book.select(page)
                application.update()
                assert page.winfo_width() > 500
            assert not tab.tabs["welding"].winfo_children()
            for kind in ("report", "receipt"):
                doc = DocumentPreview(tab, tab.order, kind)
                application.update()
                assert doc.page.winfo_height() > 200
                html = document_html(tab.order, kind)
                assert "Стеллаж" in html and "ИТОГО" in html
                doc.destroy()
            # Old calculation remains available.
            legacy = application.quick_order_tab.calculate(show_error=False)
            assert legacy and legacy["totals"]["grand"] > 0
            legacy_preview = DocumentPreview(tab, legacy, "report")
            application.update()
            legacy_preview.destroy()
            assert not errors, errors
            return f"OK\nstages=34\ntabs=10\nparts={tab.order['calculation']['quantity']}\ntotal={tab.order['totals']['grand']}\nchecks=dialogs,calculations,flags,draft,history,documents,legacy\n"
        finally:
            if application is not None:
                if application.order_tab.pending:
                    application.after_cancel(application.order_tab.pending)
                application.db.close()
                application.destroy()
            app_module.DATA_DIR = previous_data
