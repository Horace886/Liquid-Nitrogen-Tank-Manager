"""Measure cold/warm box navigation using isolated temporary inventory."""
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liquid_nitrogen_tank_manager import FreezerManagerApp
from liquid_nitrogen_tank_store import FreezerRepository, BoxSample

with TemporaryDirectory() as folder:
    repo = FreezerRepository(Path(folder) / "test.db", Path(folder) / "missing.json")
    repo.batch_set_box_samples("21", [f"{r}{c}" for r in "ABCDE" for c in range(1, 10)],
                               BoxSample(sample_name="RAW264.7", stored_by="test", stored_date="2026-09-17"))
    app = FreezerManagerApp(repo)
    app.geometry("1240x800")

    def settle():
        deadline = time.perf_counter() + 5
        while time.perf_counter() < deadline:
            app.update()
            if app._visible_content is app.content and app.box_auto_fit_job is None:
                return
            time.sleep(0.005)
        raise RuntimeError("page did not settle")

    settle()
    original = app._apply_box_grid_zoom
    runs = []

    def measured(code):
        start = time.perf_counter()
        visible = app._visible_content is app.content
        level = app.box_zoom_level
        original(code)
        runs.append((level, round((time.perf_counter() - start) * 1000, 1), visible))

    app._apply_box_grid_zoom = measured
    for label in ("cold", "warm", "cold_again"):
        app.show_compartment(2, 1)
        settle()
        if label == "cold_again":
            app.box_zoom_size_cache.clear()
        runs.clear()
        start = time.perf_counter()
        app.show_box("21")
        settle()
        print(label, "elapsed_ms", round((time.perf_counter() - start) * 1000, 1),
              "zoom_passes(level,ms,already_visible)", runs, flush=True)
    for size in (9, 10, 20):
        repo.configure_box("21", size, size)
        app.show_box("21")
        settle()
        app.fit_box_grid_to_view("21")
        for level in range(-1, 6):
            app.box_zoom_level = level
            original("21")
            actual = (app.box_grid_widget.winfo_reqwidth(), app.box_grid_widget.winfo_reqheight())
            expected = app.box_zoom_size_cache[(size, size, level)]
            assert actual == expected, (size, level, actual, expected)
    print("probe_dimensions_match_live_grid: 9x9, 10x10, 20x20 at all seven zoom levels", flush=True)
    app.destroy()
