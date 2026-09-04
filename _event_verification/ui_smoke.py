from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import time
import tkinter as tk
from datetime import date
from tkinter import ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import liquid_nitrogen_tank_manager as manager
from liquid_nitrogen_tank_manager import (
    BatchOutboundDialog,
    BoxSampleDialog,
    DatePickerField,
    FreezerManagerApp,
    InventoryEventExportDialog,
    InventoryLocationsDialog,
    MultiSelectDropdown,
    RoundedButton,
)
from liquid_nitrogen_tank_store import BoxSample, FreezerRepository


def descendants(widget: tk.Misc) -> list[tk.Misc]:
    result: list[tk.Misc] = []
    pending = list(widget.winfo_children())
    while pending:
        child = pending.pop()
        result.append(child)
        pending.extend(child.winfo_children())
    return result


with TemporaryDirectory() as folder:
    root = Path(folder)
    repository = FreezerRepository(root / "storage.db", root / "missing.json")
    repository.batch_set_box_samples(
        "11",
        ["A1", "A2"],
        BoxSample(
            sample_name="A549",
            sample_type="人肺癌细胞",
            stored_date="2026-08-14",
            stored_by="Horace",
        ),
    )
    repository.move_box_sample("11", "A1", "B1")
    repository.batch_checkout_box_samples("11", ["A2"], "Horace", "2026-08-14")
    first_freezer_id = repository.current_freezer_id
    second_freezer_id = repository.add_freezer("液氮罐 2")
    repository.set_box_sample(
        "11",
        "A1",
        BoxSample(
            sample_name="A549",
            sample_type="人肺癌细胞",
            stored_date="2026-08-14",
            stored_by="Horace",
        ),
    )
    repository.switch_freezer(first_freezer_id)

    app = FreezerManagerApp(repository)
    app.withdraw()
    for _ in range(12):
        app.update()
        time.sleep(0.025)
    startup_dialogs = [child for child in app.winfo_children() if isinstance(child, tk.Toplevel)]
    if startup_dialogs or app.grab_current() is not None:
        raise RuntimeError("cold startup unexpectedly opened a modal dialog")
    app.show_overview()
    app.update_idletasks()
    overview_actions = {
        child._text
        for child in descendants(app.content)
        if isinstance(child, RoundedButton)
    }
    if "调整规格" not in overview_actions or {"＋ 添加一列", "去除一列"} & overview_actions:
        raise RuntimeError(f"tank specification actions are incorrect: {overview_actions}")
    spec_dialog = manager.StorageSpecDialog(app, 4, 5)
    spec_dialog.columns_var.set("7")
    spec_dialog.layers_var.set("6")
    spec_dialog._refresh_preview()
    if "42个盒位" not in str(spec_dialog.preview_label.cget("text")):
        raise RuntimeError("tank specification preview did not update its capacity")
    spec_dialog._confirm()
    app.update()
    if spec_dialog.result != (7, 6):
        raise RuntimeError(f"tank specification result is incorrect: {spec_dialog.result}")
    repository.configure_storage(7, 6)
    app.overview_batch_mode = True
    app.show_overview()
    app.update_idletasks()
    if len(app.overview_compartment_buttons) != 42:
        raise RuntimeError("7-column by 6-layer overview did not render all box positions")
    app.overview_batch_mode = False
    app.selected_compartments.clear()
    repository.configure_storage(4, 5)
    app.show_box("11")
    app.update_idletasks()
    fit_buttons = [
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "适应窗口"
    ]
    if len(fit_buttons) != 1:
        raise RuntimeError(f"box auto-fit action missing: {len(fit_buttons)}")
    zoom_buttons = [
        child for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text in {"− 缩小", "＋ 放大", "适应窗口"}
    ]
    if len(zoom_buttons) != 3 or any(button._focus_outline for button in zoom_buttons):
        raise RuntimeError("box zoom controls still display a focus outline")
    if any(str(button.cget("takefocus")) != "1" for button in zoom_buttons):
        raise RuntimeError("box zoom controls lost keyboard accessibility")
    if app.bind_all("<Control-MouseWheel>"):
        raise RuntimeError("Ctrl + mouse-wheel box zoom binding is still active")
    ctrl_zoom_hints = [
        child
        for child in descendants(app.content)
        if isinstance(child, tk.Label) and "Ctrl + 鼠标滑轮" in str(child.cget("text"))
    ]
    if ctrl_zoom_hints:
        raise RuntimeError("obsolete Ctrl + mouse-wheel zoom hint is still visible")
    if app.box_legend_widget is None or app.box_legend_widget.winfo_manager() != "pack":
        raise RuntimeError("box legend is not mounted as a fixed footer")
    if app.box_grid_canvas is None or app.box_grid_canvas.winfo_manager() != "grid":
        raise RuntimeError("box grid is not mounted in its independent scroll viewport")
    if app.box_move_undo_button is not None:
        raise RuntimeError("quick-move undo action is visible while quick move is disabled")
    app.toggle_box_quick_move_mode("11")
    app.update_idletasks()
    if app.box_move_undo_button is None or not app.box_move_undo_button.winfo_exists():
        raise RuntimeError("quick-move undo action did not appear with quick move enabled")
    app.toggle_box_quick_move_mode("11")
    app.update_idletasks()
    if app.box_move_undo_button is not None:
        raise RuntimeError("quick-move undo action remained visible after quick move was disabled")
    app.box_batch_mode = True
    app.box_quick_move_mode = True
    app.selected_box_positions = {"A1"}
    repository.switch_freezer(second_freezer_id)
    app.show_box("11")
    if app.box_batch_mode or app.box_quick_move_mode or app.selected_box_positions:
        raise RuntimeError("box interaction state leaked into the same box code in another tank")
    if app.current_box_freezer_id != second_freezer_id:
        raise RuntimeError("current box owner was not updated after switching tanks")
    repository.switch_freezer(first_freezer_id)
    app.show_box("11")
    app.box_batch_mode = True
    app.box_quick_move_mode = True
    app.selected_box_positions = {"A1", "B1"}
    back_to_location = next(
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "←  返回冻存盒位 11"
    )
    back_to_location._command()
    if app.box_batch_mode or app.box_quick_move_mode or app.selected_box_positions:
        raise RuntimeError("box Back action did not leave transient box interaction modes")
    app.show_box("11")
    app.box_batch_mode = True
    app.selected_box_positions = {"A1"}
    app.show_box("11")
    if not app.box_batch_mode or app.selected_box_positions != {"A1"}:
        raise RuntimeError("same-box refresh unexpectedly left its interaction mode")
    app.show_inventory_page()
    if app.box_batch_mode or app.box_quick_move_mode or app.selected_box_positions:
        raise RuntimeError("leaving the box page did not clear its interaction modes")
    app.show_box("11")
    app.box_batch_mode = True
    app.box_quick_move_mode = True
    app.selected_box_positions = {"A1", "B1"}
    app.box_drag_source = "B1"
    app.box_drag_target = "A1"
    app.nav_buttons["search"]._command()
    if (
        app.box_batch_mode
        or app.box_quick_move_mode
        or app.selected_box_positions
        or app.box_drag_source is not None
        or app.box_drag_target is not None
    ):
        raise RuntimeError("workbench navigation did not leave transient box interaction modes")
    app.update_idletasks()
    if app.page_title_var.get() != "查找细胞 · 全部液氮罐":
        raise RuntimeError(f"basic cell search is not global: {app.page_title_var.get()}")

    outbound_dialog = BatchOutboundDialog(app, 1, "液氮罐 1 / 冻存盒 11/A1")
    app.update_idletasks()
    compact_dialog_height = outbound_dialog.winfo_reqheight()
    outbound_pickers = [child for child in descendants(outbound_dialog) if isinstance(child, DatePickerField)]
    if len(outbound_pickers) != 1:
        raise RuntimeError(f"expected one outbound date picker, found {len(outbound_pickers)}")
    outbound_picker = outbound_pickers[0]
    # November 2026 spans six calendar rows and previously clipped the final row.
    outbound_picker.variable.set("2026-11-01")
    outbound_picker.open_calendar()
    app.update_idletasks()
    if outbound_picker.panel is None or not outbound_picker.panel.winfo_exists():
        raise RuntimeError("outbound visual date calendar did not open")
    if not isinstance(outbound_picker.panel, tk.Toplevel):
        raise RuntimeError("date calendar is not an independent top-level popup")
    panel_min_width, panel_min_height = outbound_picker.panel.minsize()
    if panel_min_width < 292 or panel_min_height < outbound_picker.panel.winfo_reqheight():
        raise RuntimeError("six-week top-level calendar does not reserve its full requested size")
    if compact_dialog_height >= panel_min_height + 60:
        raise RuntimeError("outbound dialog was unnecessarily enlarged to contain the calendar")
    outbound_dialog.destroy()

    export_dialog = InventoryEventExportDialog(app)
    app.update_idletasks()
    today = date.today().isoformat()
    if export_dialog.date_from_var.get() != today or export_dialog.date_to_var.get() != today:
        raise RuntimeError("event export date defaults are incorrect")
    export_pickers = [child for child in descendants(export_dialog) if isinstance(child, DatePickerField)]
    if len(export_pickers) != 2:
        raise RuntimeError(f"expected two visual export date pickers, found {len(export_pickers)}")
    if any(picker.winfo_reqwidth() > 210 for picker in export_pickers):
        raise RuntimeError("export date field requests too much width and can clip its date button")
    from_picker = next(picker for picker in export_pickers if picker.variable is export_dialog.date_from_var)
    from_picker.open_calendar()
    app.update_idletasks()
    if from_picker.panel is None or not from_picker.panel.winfo_exists():
        raise RuntimeError("visual date calendar did not open")
    if app.grab_current() is not None:
        raise RuntimeError("visual date calendar unexpectedly grabbed the application")
    first_day = date.today().replace(day=1).isoformat()
    from_picker._select_date(date.today().replace(day=1))
    export_dialog._save()
    if export_dialog.result != (first_day, today):
        raise RuntimeError(f"event export date result is incorrect: {export_dialog.result}")

    app.search_var.set("A549")
    app.show_search_results()
    app.update_idletasks()
    app.open_inventory_location(second_freezer_id, "11", ("A1",), return_mode="basic")
    app.update_idletasks()
    if app.repository.current_freezer_id != second_freezer_id or app.box_search_highlights != {"A1"}:
        raise RuntimeError("global search did not switch tanks and highlight the matched position")
    back_buttons = [
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "←  返回搜索结果"
    ]
    if len(back_buttons) != 1:
        raise RuntimeError(f"search-aware box back action missing: {len(back_buttons)}")
    back_buttons[0]._command()
    app.update_idletasks()
    visited_labels = [
        child
        for child in descendants(app.content)
        if isinstance(child, tk.Label) and str(child.cget("text")) == "已查看"
    ]
    revisit_buttons = [
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "再次打开"
    ]
    if len(visited_labels) != 1 or len(revisit_buttons) != 1:
        raise RuntimeError(
            f"opened search result is not visibly marked: labels={len(visited_labels)}, buttons={len(revisit_buttons)}"
        )

    app.empty_required_var.set("3")
    app.empty_scope_var.set("全部液氮罐")
    app.empty_arrangement_var.set("连续优先")
    empty_results = repository.find_empty_positions(3, all_freezers=True, continuous=True)
    empty_target = next(item for item in empty_results if item["fits"])
    app.show_empty_search_page(run_search=True)
    app.update_idletasks()
    recommended_positions = list(empty_target["recommended_positions"])
    app.open_recommended_empty_positions(
        str(empty_target["freezer_id"]),
        str(empty_target["code"]),
        recommended_positions,
    )
    app.update_idletasks()
    if app.box_search_return_mode != "empty" or app.selected_box_positions != set(recommended_positions):
        raise RuntimeError("empty-position result did not highlight and select its recommended positions")
    empty_back_buttons = [
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "←  返回查找空位"
    ]
    if len(empty_back_buttons) != 1:
        raise RuntimeError(f"empty-search-aware box back action missing: {len(empty_back_buttons)}")
    empty_back_buttons[0]._command()
    app.update_idletasks()
    opened_empty_labels = [
        child
        for child in descendants(app.content)
        if isinstance(child, tk.Label) and str(child.cget("text")) == "已查看"
    ]
    opened_empty_buttons = [
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "再次打开"
    ]
    if len(opened_empty_labels) != 1 or len(opened_empty_buttons) != 1:
        raise RuntimeError(
            "opened empty-position result is not visibly marked: "
            f"labels={len(opened_empty_labels)}, buttons={len(opened_empty_buttons)}"
        )
    repository.switch_freezer(first_freezer_id)
    app.inventory_location_sample_name = "A549"
    app.inventory_location_opened_boxes.clear()
    location_rows = repository.inventory_locations("A549")
    location_dialog = InventoryLocationsDialog(app, "A549", location_rows)
    app.update_idletasks()
    location_target = location_rows[0]
    target_positions = tuple(str(value) for value in location_target["positions"])
    location_dialog._open_box(
        str(location_target["freezer_id"]),
        str(location_target["code"]),
        target_positions,
    )
    for _ in range(3):
        app.update()
    if app.box_search_return_mode != "locations" or app.box_search_highlights != set(target_positions):
        raise RuntimeError("inventory location did not open with search-style position highlighting")
    location_back_buttons = [
        child
        for child in descendants(app.content)
        if isinstance(child, RoundedButton) and child._text == "←  返回细胞位置"
    ]
    if len(location_back_buttons) != 1:
        raise RuntimeError(f"inventory-location-aware back action missing: {len(location_back_buttons)}")
    app.show_inventory_page()
    returned_locations = repository.inventory_locations("A549")
    returned_dialog = InventoryLocationsDialog(app, "A549", returned_locations)
    app.update_idletasks()
    returned_labels = [
        child
        for child in descendants(returned_dialog)
        if isinstance(child, tk.Label) and str(child.cget("text")) == "已查看"
    ]
    returned_buttons = [
        child
        for child in descendants(returned_dialog)
        if isinstance(child, RoundedButton) and child._text == "再次打开"
    ]
    if len(returned_labels) != 1 or len(returned_buttons) != 1:
        raise RuntimeError(
            "visited inventory location is not visibly marked: "
            f"labels={len(returned_labels)}, buttons={len(returned_buttons)}"
        )
    returned_dialog._close()
    app.update()
    repository.switch_freezer(first_freezer_id)
    empty_dialog = BoxSampleDialog(
        app,
        "入库细胞 · 11/C1",
        BoxSample(stored_date="2026-08-14"),
    )
    app.update_idletasks()
    if len([child for child in descendants(empty_dialog) if isinstance(child, DatePickerField)]) != 1:
        raise RuntimeError("cell inbound dialog is missing its visual date picker")
    empty_actions = {child._text for child in descendants(empty_dialog) if isinstance(child, RoundedButton)}
    if "确认入库" not in empty_actions:
        raise RuntimeError(f"single-position inbound action missing: {empty_actions}")
    # Simulate Microsoft Pinyin owning the first Return while committing the
    # literal candidate "raw".  The first Return must only commit text; the
    # immediately following Return must submit without entering a window menu.
    empty_dialog.variables["sample_name"].set("raw")
    empty_dialog.variables["stored_by"].set("Horace")
    sample_name_entry = empty_dialog.entries["sample_name"]
    original_composition_active = manager._ime_composition_active
    original_complete_composition = manager._complete_ime_composition
    composition_checks = [0]

    def simulated_composition_active(_widget: tk.Misc) -> bool:
        composition_checks[0] += 1
        return composition_checks[0] == 1

    manager._ime_composition_active = simulated_composition_active
    manager._complete_ime_composition = lambda _widget: True
    try:
        return_event = tk.Event()
        return_event.state = 0
        sample_name_entry._ime_return_handler(return_event)
        app.update()
        if empty_dialog.result is not None or not empty_dialog.winfo_exists():
            raise RuntimeError("IME candidate-confirming Return unexpectedly submitted the inbound form")
        sample_name_entry._ime_return_handler(return_event)
        for _ in range(12):
            app.update()
            time.sleep(0.03)
    finally:
        manager._ime_composition_active = original_composition_active
        manager._complete_ime_composition = original_complete_composition
    if empty_dialog.result is None or empty_dialog.result.get("sample_name") != "raw":
        raise RuntimeError(f"second Return did not submit committed IME text: {empty_dialog.result}")
    if empty_dialog.winfo_exists():
        empty_dialog.destroy()
    occupied_dialog = BoxSampleDialog(
        app,
        "编辑细胞 · 11/B1",
        repository.get_box_sample("11", "B1"),
        allow_clear=True,
    )
    app.update_idletasks()
    occupied_actions = {child._text for child in descendants(occupied_dialog) if isinstance(child, RoundedButton)}
    if not {"保存信息", "出库", "清除孔位"}.issubset(occupied_actions):
        raise RuntimeError(f"single-position actions missing: {occupied_actions}")
    occupied_dialog.destroy()
    app.show_inventory_events_page()
    app.update_idletasks()
    event_date_pickers = [
        child for child in descendants(app.content) if isinstance(child, DatePickerField)
    ]
    if len(event_date_pickers) != 2:
        raise RuntimeError(f"event filters are missing visual date pickers: {len(event_date_pickers)}")
    if {
        id(picker.variable) for picker in event_date_pickers
    } != {id(app.event_date_from_var), id(app.event_date_to_var)}:
        raise RuntimeError("event date pickers are not connected to both filter variables")
    event_date_pickers[0].open_calendar()
    app.update_idletasks()
    if not isinstance(event_date_pickers[0].panel, tk.Toplevel):
        raise RuntimeError("event-filter calendar did not open as a top-level popup")
    event_date_pickers[0].close_calendar()
    event_trees = [child for child in descendants(app.content) if isinstance(child, ttk.Treeview)]
    if len(event_trees) != 1:
        raise RuntimeError(f"expected one inventory event table, found {len(event_trees)}")
    event_rows = [event_trees[0].item(item, "values") for item in event_trees[0].get_children()]
    move_rows = [row for row in event_rows if row and row[0] == "入库（快捷移动）"]
    if not move_rows or any(row[3] for row in move_rows):
        raise RuntimeError(f"anonymous move operation unexpectedly shows a person: {move_rows}")
    named_rows = [row for row in event_rows if row and row[0] in {"入库", "出库"}]
    if not named_rows or any(not row[3] or row[3] == "—" for row in named_rows):
        raise RuntimeError(f"named inbound/outbound operation is missing a person: {named_rows}")
    action_filters = [child for child in descendants(app.content) if isinstance(child, MultiSelectDropdown)]
    if len(action_filters) != 1:
        raise RuntimeError(f"expected one multi-select action filter, found {len(action_filters)}")
    page_height_before = app.content.winfo_reqheight()
    action_filters[0].button.invoke()
    app.update()
    if app.grab_current() is not None:
        raise RuntimeError("operation filter menu unexpectedly grabbed the application")
    if action_filters[0].panel is None or not action_filters[0].panel.winfo_exists():
        raise RuntimeError("operation filter panel did not open")
    if app.content.winfo_reqheight() != page_height_before:
        raise RuntimeError("operation filter panel unexpectedly stretched the page")
    panel_buttons = {
        str(child.cget("text"))
        for child in descendants(action_filters[0].panel)
        if isinstance(child, ttk.Button)
    }
    if not {"全选", "清空", "完成"}.issubset(panel_buttons):
        raise RuntimeError(f"operation filter actions are incomplete: {panel_buttons}")
    action_filters[0]._set_all(False)
    action_filters[0].variables["IN"].set(True)
    action_filters[0].variables["OUT"].set(True)
    action_filters[0]._sync_selection()
    if app.event_action_filters != {"IN", "OUT"}:
        raise RuntimeError(f"multi-select action filter failed: {app.event_action_filters}")
    action_filters[0]._close()
    app.reset_inventory_event_filters()
    app.update_idletasks()
    app.show_box("11")
    app.update_idletasks()
    print(
        "ui_smoke_ok",
        f"events={len(repository.inventory_events)}",
        f"tubes={sum(sample.occupied for sample in repository.box_samples.values())}",
    )
    app.destroy()
