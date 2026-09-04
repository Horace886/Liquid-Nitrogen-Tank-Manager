"""Bilingual UI regression on temporary data; never opens the user's database."""
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
import tkinter as tk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import liquid_nitrogen_tank_manager as manager
import liquid_nitrogen_tank_ui as ui
from liquid_nitrogen_tank_i18n import msg, render
from liquid_nitrogen_tank_store import BoxSample, FreezerRepository


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


def settle(app):
    for _ in range(6):
        app.update()
        time.sleep(.025)


with TemporaryDirectory() as folder:
    path = Path(folder)
    repo = FreezerRepository(path / 'storage.db', path / 'missing.json')
    repo.set_box_sample('11', 'A1', BoxSample(sample_name='入库', sample_type='中文类别',
                                            stored_by='Horace', stored_date='2026-08-19', notes='用户备注'))
    app = manager.FreezerManagerApp(repo)
    failures = []
    app.report_callback_exception = lambda *error: failures.append(error)
    app.geometry('1240x780')
    settle(app)
    original = repo.path.read_bytes()
    assert app.change_language('en')
    settle(app)
    assert app.title() == 'LN₂ Manager'
    assert ui.display(app, '入库') == '入库'
    app.show_search_page()
    app.search_var.set('入库')
    app.search_opened_boxes.add((repo.current_freezer_id, '11'))
    content = app.content
    assert app.change_language('zh') and app.change_language('en')
    assert app.content is content and app.search_var.get() == '入库'
    assert (repo.current_freezer_id, '11') in app.search_opened_boxes
    for page in (app.show_overview, app.show_search_page, app.show_empty_search_page,
                 app.show_inventory_page, app.show_inventory_events_page, app.show_advanced_search_page,
                 app.show_backup_page, lambda: app.show_box('11')):
        page()
        settle(app)
        assert app.change_language('zh') and app.change_language('en')
        settle(app)
        texts = [str(w.cget('text')) for w in descendants(app.content) if isinstance(w, (ui.Label, ui.TtkLabel))]
        print(page.__name__, 'labels=', len(texts), 'chinese=', [t for t in texts if any('\u4e00' <= c <= '\u9fff' for c in t)][:12])
    var = tk.StringVar(app, value='有细胞')
    choice = ui.Combobox(app, textvariable=var, values=(msg('有细胞'), msg('空盒')), state='readonly')
    choice.current(1)
    assert var.get() == '空盒'
    choice.destroy()
    app.box_quick_move_mode = True
    box_content = app.content
    assert app.change_language('zh') and app.change_language('en')
    assert app.content is box_content and app.box_quick_move_mode
    app.show_inventory_events_page()
    app.geometry('1000x620')
    settle(app)
    for widget in descendants(app.content):
        if isinstance(widget, manager.DatePickerField):
            button = widget.calendar_button
            assert button.winfo_rootx() + button.winfo_width() <= app.winfo_rootx() + app.winfo_width()
    app.geometry('1240x780')
    settle(app)
    dialog = manager.BoxSampleDialog(app, msg('批量入库'), BoxSample(), batch=True)
    settle(app)
    assert not app.change_language('zh')
    assert app.language_state.language == 'en'
    for widget in descendants(dialog):
        if isinstance(widget, manager.RoundedButton):
            assert widget.winfo_rooty() + widget.winfo_height() <= dialog.winfo_rooty() + dialog.winfo_height(), widget._text
    dialog.destroy()
    settle(app)
    # Exercise the actual UI export path with a temporary destination. The
    # standard Excel reader must accept the selected export language.
    from unittest.mock import patch
    from liquid_nitrogen_tank_excel import preview_freezer_workbook
    output = path / 'inventory.xlsx'
    with patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(output)), patch.object(app, '_notify'):
        app.export_to_excel()
    preview = preview_freezer_workbook(output)
    assert preview.can_import and preview.freezers[0][1]['11/A1']['sample_name'] == '入库'
    import zipfile
    import liquid_nitrogen_tank_excel as excel
    with zipfile.ZipFile(output) as book:
        assert 'Cell Name' in book.read('xl/worksheets/sheet2.xml').decode('utf-8')
    # The activity export has its own date dialog and must also receive the
    # language, independent of the operation filters currently shown.
    for language in ('zh', 'en'):
        app.change_language(language)
        events_output = path / f'events-{language}.xlsx'
        with patch.object(manager.InventoryEventExportDialog, 'show', return_value=('2026-08-01', '2026-08-31')), \
             patch.object(ui.filedialog, 'asksaveasfilename', return_value=str(events_output)) as save_dialog, \
             patch.object(app, '_notify'):
            app.export_inventory_events_to_excel()
        with zipfile.ZipFile(events_output) as book:
            rows = excel._sheet_rows(book, 'xl/worksheets/sheet1.xml', excel._shared_strings(book))
            assert rows[1][1] == ('Operation Date' if language == 'en' else '操作日期')
            assert rows[2][9] == '用户备注'
        filename = save_dialog.call_args.kwargs['initialfile']
        assert filename.startswith('Stock_Activity_' if language == 'en' else '出入库登记_')
        for widget in list(app.winfo_children()):
            if isinstance(widget, tk.Toplevel):
                widget.destroy()
        settle(app)
    assert repo.path.read_bytes() == original
    assert not failures, failures
    app.destroy()
    app = manager.FreezerManagerApp(repo)
    assert app.language_state.language == 'en'
    app.destroy()
print('i18n_smoke_ok')
