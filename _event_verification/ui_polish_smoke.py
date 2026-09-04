"""UI polish checks; --preview keeps a temporary-data window open for inspection."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from liquid_nitrogen_tank_manager import COLORS, FreezerManagerApp, RoundedButton
from liquid_nitrogen_tank_store import BoxSample, FreezerRepository


def settle(app, seconds=0.25):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.update()
        time.sleep(0.01)


with TemporaryDirectory() as folder:
    repository = FreezerRepository(Path(folder) / "preview.db", Path(folder) / "missing.json")
    for code, name in (("11", "A549"), ("21", "HEK293T"), ("32", "HeLa")):
        repository.batch_set_box_samples(
            code, ["A1", "A2", "B1"],
            BoxSample(sample_name=name, stored_by="演示人员", stored_date="2026-09-04"),
        )
    app = FreezerManagerApp(repository)
    app.title("界面优化预览 · 临时演示数据")
    errors = []
    app.report_callback_exception = lambda *error: errors.append(error)
    try:
        app.geometry("1000x620")
        settle(app)
        pending = [app]
        while pending:
            widget = pending.pop()
            assert "text" not in widget.keys() or str(widget.cget("text")) != "界面动效", "removed motion toggle is still visible"
            pending.extend(widget.winfo_children())
        assert app.nav_buttons["overview"]._normal_fill == COLORS["primary"]
        assert abs(float(app.sidebar_capacity_bar["value"]) - 9 / repository.tube_capacity * 100) < 0.001
        bar = app.sidebar_capacity_bar
        assert bar.winfo_y() + bar.winfo_height() <= bar.master.winfo_height(), "capacity bar clipped"
        assert app.nav_buttons["backup"].winfo_viewable(), "last navigation action hidden"
        backup = app.nav_buttons["backup"]
        backup.focus_force()
        settle(app, 0.05)
        assert backup.itemcget("surface", "outline") == backup._border
        assert backup.itemcget("surface", "fill") == backup._hover_fill
        nav_canvas = app.sidebar_scroller.canvas
        assert nav_canvas.canvasy(0) <= backup.winfo_y()
        assert backup.winfo_y() + backup.winfo_height() <= nav_canvas.canvasy(0) + nav_canvas.winfo_height() + 2
        app.nav_buttons["overview"].focus_force()
        settle(app, 0.05)
        overview = app.nav_buttons["overview"]
        assert overview.itemcget("surface", "outline") == COLORS["primary"]
        assert overview.itemcget("surface", "width") == "1.0"
        assert overview.itemcget("surface", "fill") == overview._hover_fill

        calls = []
        button = RoundedButton(app, text="测试", command=lambda: calls.append(1), fill="#000000", hover_fill="#FFFFFF")
        button.place(x=700, y=100, width=100, height=40)
        settle(app, 0.05)
        button._on_enter(None)
        settle(app, 0.04)
        assert button._fill not in ("#000000", "#FFFFFF"), "hover did not interpolate"
        button._on_leave(None)
        settle(app)
        assert button._fill == "#000000", "interrupted hover did not return to normal"
        button._on_enter(None)
        button.set_palette(fill="#112233", foreground="white", hover_fill="#334455", border="#112233")
        settle(app)
        assert button._fill == "#334455", "stale animation overwrote the new palette"
        button._on_leave(None)
        settle(app)
        assert button._fill == "#112233" and button._animation_job is None
        button.focus_force()
        settle(app, 0.05)
        assert button.itemcget("surface", "width") == "2.0", "keyboard focus not visible"
        button._invoke()
        button._invoke()
        settle(app)
        assert calls == [1], "duplicate activation"
        button._on_enter(None)
        job = button._animation_job
        button.destroy()
        assert job not in app.tk.call("after", "info"), "destroy left an animation callback"
        for show in (app.show_inventory_page, app.show_inventory_events_page, lambda: app.show_box("11"), app.show_overview):
            show()
            settle(app)
        assert not errors, errors
        print("ui_polish_ok: animation, interruption, focus, cleanup, navigation, capacity", flush=True)
        if "--preview" in sys.argv:
            if "--compact" not in sys.argv:
                app.geometry("1240x780")
            app.after(300000, app.destroy)
            app.mainloop()
    finally:
        try:
            app.destroy()
        except tk.TclError:
            pass
