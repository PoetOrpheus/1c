"""GUI smoke checks used by the portable application's --self-test switch."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import tkinter as tk
import time

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


def run_gui_checks(output_dir, fixture_dir=None):
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
            manager = application.references_tab.managers[0]
            first = manager.selected()
            manager.search_var.set(first["name"])
            application.update()
            assert manager.tree.get_children()
            manager.inline_vars["price_sheet"].set("nan")
            manager.save_inline()
            assert manager.inline_error.get()
            manager.inline_vars["price_sheet"].set(str(first["price_sheet"]))
            manager.save_inline()
            assert not manager.inline_error.get()
            manager.search_var.set("")
            application.select_tab(application.references_tab)
            application.update()
            assert application.notebook.select() == str(application.references_tab)
            application.select_tab(tab)
            order = sample_specification(application.db)
            tab.load_order(order, None)
            application.update()
            assert len(tab.stages_tree.get_children()) == 34
            assert len(tab.tabs) == 10
            assert tab.calculate()["calculation"]["quantity"] == 16
            second = application.new_order_tab()
            assert second.order["number"] != tab.order["number"]
            second.customer_var.set("Другой заказчик")
            assert second.flush()
            assert tab.order["customer"] != second.order["customer"]
            assert application.db.get_state(second.draft_key)["order"]["customer"] == "Другой заказчик"
            application.select_stage("painting")
            assert second.book.select() == str(second.tabs["painting"])
            assert application.close_order_tab(second, confirm=False)
            assert application.order_tab is tab
            tab.tree.selection_set(order["nodes"][1]["id"])
            application.update()
            assert tab.node_title.get() == "Полка"
            tab.edit_finance()
            finance_dialog = next(w for w in tab.winfo_children() if isinstance(w, FormDialog))
            finance_dialog.variables["vat"].set("22")
            finance_dialog.variables["material_markup"].set("10")
            finance_dialog.variables["work_markup"].set("20")
            finance_dialog.save()
            assert tab.flush()
            assert tab.order["finance"]["vat"] == 22
            from costing import cost_document
            assert cost_document(tab.order)["totals"]["Всего"]["sale"] > cost_document(tab.order)["totals"]["Всего"]["cost"]

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
                assert "Стеллаж" in html and "Всего сумма заказа с НДС:" in html
                doc.destroy()
            # Old calculation remains available.
            from app import OrderTab
            legacy_tab = OrderTab(application, application.db, application.set_status, application.history_tab.refresh)
            legacy = legacy_tab.calculate(show_error=False)
            assert legacy and legacy["totals"]["grand"] > 0
            legacy_preview = DocumentPreview(tab, legacy, "report")
            application.update()
            legacy_preview.destroy()
            legacy_tab.destroy()
            import_summary = ""
            if fixture_dir:
                from cam_import import read_batch
                from cam_import_ui import ImportPreview
                batch = read_batch(Path(fixture_dir).iterdir())
                assert not batch["errors"], batch["errors"]
                assert len(batch["documents"]) == 9
                assert len(batch["duplicates"]) == 4
                tab.load_order(new_order(application.db.next_order_number()), None)
                # Preview/cancel must not create a single position or database order.
                untouched = deepcopy(tab.order)
                tab.start_import(list(Path(fixture_dir).iterdir()))
                deadline = time.monotonic() + 20
                preview = None
                while time.monotonic() < deadline:
                    application.update()
                    preview = next((w for w in tab.winfo_children() if isinstance(w, ImportPreview)), None)
                    if preview:
                        break
                    time.sleep(.02)
                assert preview is not None, "Фоновое чтение не открыло просмотр импорта"
                assert len(preview.parts_tree.get_children()) == 41
                preview.destroy()
                assert tab.order == untouched
                preview = ImportPreview(tab, batch)
                preview.docs_tree.selection_set(batch["documents"][0]["key"])
                preview.edit_document()
                dialog = next(w for w in preview.winfo_children() if isinstance(w, FormDialog))
                dialog.variables["price"].set("1000")
                dialog.save()
                assert preview.grab_current() == preview
                application.update()
                preview.apply()
                application.update()
                assert len(tab.order["laser_rows"]) == 9
                assert len(tab.order["laser_unmatched"]) == 45
                assert tab.order["calculation"]["quantity"] == 152
                assert len(tab.order["nodes"]) == 42
                assert tab.order["totals"]["material"] == 1000
                tab.save_order()
                restored = application.db.get_order(tab.saved_id)
                tab.load_order(restored, tab.saved_id)
                assert len(tab.order["cam_imports"]) == 9
                again = ImportPreview(tab, batch)
                assert not any(again.included.values())
                again.destroy()
                for kind in ("report", "receipt"):
                    doc = DocumentPreview(tab, tab.order, kind)
                    application.update()
                    assert "Sekirus.pdf" in document_html(tab.order, "report")
                    doc.destroy()
                import_summary = "import=13 files,9 nests,41 unique parts,152 pieces,4 duplicate pairs; cancel,preview,pricing,history,documents=OK\n"
            library = application.references_tab.parts_manager
            application.show_section("references")
            application.references_tab.book.select(library)
            library.add_folder()
            folder_dialog = next(w for w in library.winfo_children() if isinstance(w, FormDialog))
            folder_dialog.variables["name"].set("Шкаф для проверки")
            folder_dialog.save()
            library.edit_part()
            part_dialog = next(w for w in library.winfo_children() if isinstance(w, FormDialog))
            part_dialog.variables["name"].set("Полка из справочника")
            part_dialog.save()
            application.update()
            assert len(library.parts) == 1
            if fixture_dir:
                from part_catalog import import_candidates
                preview = library.preview_import(batch, import_candidates(batch), library.destination())
                preview.import_tree.selection_set(preview.import_tree.get_children()[:2])
                preview.confirm_import()
                assert len(library.parts) == 3
            scratch = application.new_order_tab()
            library.tree.selection_set("p" + str(next(iter(library.parts))))
            library.add_to_order()
            assert len(scratch.order["nodes"]) == 1
            assert scratch.order["nodes"][0]["catalog_id"]
            assert application.close_order_tab(scratch, confirm=False)
            assert not errors, errors
            from ui_checks import check_scrolling
            check_scrolling(application)
            assert not errors, errors
            return f"OK\nstages=34\ntabs=10\nparts={tab.order['calculation']['quantity']}\ntotal={tab.order['totals']['grand']}\nchecks=dialogs,calculations,flags,draft,history,documents,legacy\n" + import_summary
        finally:
            if application is not None:
                if application.order_tab.pending:
                    application.after_cancel(application.order_tab.pending)
                application.db.close()
                application.destroy()
            app_module.DATA_DIR = previous_data
