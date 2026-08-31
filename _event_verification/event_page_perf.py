from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liquid_nitrogen_tank_manager import FreezerManagerApp
from liquid_nitrogen_tank_store import FreezerRepository


def descendants(widget: tk.Misc) -> list[tk.Misc]:
    result: list[tk.Misc] = []
    pending = list(widget.winfo_children())
    while pending:
        child = pending.pop()
        result.append(child)
        pending.extend(child.winfo_children())
    return result


with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    database = Path(folder) / "storage.db"
    with sqlite3.connect(ROOT / "liquid_nitrogen_tank_storage.db") as source, sqlite3.connect(database) as target:
        source.backup(target)
    repository = FreezerRepository(database, Path(folder) / "missing.json")
    app = FreezerManagerApp(repository)
    app.attributes("-alpha", 0.0)
    app.deiconify()
    app.update()
    started = time.perf_counter()
    app.show_inventory_events_page()
    app.update_idletasks()
    elapsed = time.perf_counter() - started
    widgets = descendants(app.content)
    trees = [widget for widget in widgets if isinstance(widget, ttk.Treeview)]
    if len(trees) != 1:
        raise RuntimeError(f"expected one event tree, found {len(trees)}")
    if "batch" in trees[0]["columns"] or tuple(trees[0]["columns"]) != (
        "action", "sample", "location", "operator", "business_date", "created_at"
    ):
        raise RuntimeError(f"unexpected event columns: {trees[0]['columns']}")
    displayed = len(trees[0].get_children())
    total_events = repository.count_inventory_events()
    if displayed != min(300, total_events):
        raise RuntimeError(f"unexpected displayed event count: {displayed}")
    filter_inputs = [
        widget
        for widget in widgets
        if isinstance(widget, (ttk.Entry, ttk.Combobox))
        or (
            isinstance(widget, ttk.Button)
            and str(widget.cget("style")) == "EventFilter.TButton"
        )
    ]
    filter_inputs.sort(key=lambda widget: widget.winfo_rootx())
    input_x = [widget.winfo_rootx() for widget in filter_inputs]
    input_y = [widget.winfo_rooty() for widget in filter_inputs]
    input_widths = [widget.winfo_width() for widget in filter_inputs]
    if len(filter_inputs) != 5 or max(input_y) - min(input_y) > 1 or max(input_widths) - min(input_widths) > 1:
        raise RuntimeError(
            f"event filters are not aligned: count={len(filter_inputs)}, x={input_x}, y={input_y}, widths={input_widths}"
        )
    print(
        "event_page_perf_ok",
        f"events={total_events}",
        f"displayed={displayed}",
        f"widgets={len(widgets)}",
        f"filter_y={input_y[0]}",
        f"filter_width={input_widths[0]}",
        f"seconds={elapsed:.3f}",
    )
    app.destroy()
