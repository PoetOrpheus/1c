"""Application entry point."""

from __future__ import annotations

import ctypes
import sys
import traceback
from pathlib import Path
from tkinter import messagebox

from app import APP_DIR, DOCUMENTS_DIR, Application, run
from reports import document_html


def self_test() -> int:
    """Exercise the packaged UI, calculator, SQLite and document generator."""
    result_path = APP_DIR / "self-test-result.txt"
    try:
        from smoke_checks import run_gui_checks
        result_path.write_text(run_gui_checks(APP_DIR), encoding="utf-8")
        return 0
    except Exception:
        result_path.write_text("FAILED\n" + traceback.format_exc(), encoding="utf-8")
        return 1


if __name__ == "__main__":
    try:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        if "--self-test" in sys.argv:
            raise SystemExit(self_test())
        run()
    except Exception:
        app_dir = (
            Path(sys.executable).resolve().parent
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parent
        )
        log_path = app_dir / "error.log"
        log_path.write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror(
            "Ошибка запуска",
            f"Приложение не смогло запуститься. Подробности записаны в:\n{log_path}",
        )
