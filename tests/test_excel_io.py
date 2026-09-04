from __future__ import annotations

import re
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

import liquid_nitrogen_tank_excel as excel

from liquid_nitrogen_tank_excel import (
    export_cryotube_workbook,
    export_freezer_workbook,
    export_inventory_events_workbook,
    preview_freezer_workbook,
)
from liquid_nitrogen_tank_store import (
    EVENT_MEMORY_CACHE_LIMIT,
    BoxSample,
    FreezerRepository,
    InventoryEvent,
    UnitRecord,
    all_unit_codes,
)


class ExcelIoTests(unittest.TestCase):
    def test_bilingual_inventory_roundtrip_preserves_user_values_and_tank_names(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample = BoxSample(sample_name='入库', sample_type='Notes', stored_date='2026-08-19',
                               stored_by='出库', notes='中文备注 & <raw>\nSecond line')
            names = ['Summary', '使用说明', '研发 "LN2" & a/b', 'instructions']
            expected = None
            for language in ('zh', 'en'):
                output = root / f'{language}.xlsx'
                export_cryotube_workbook(output, [(name, {('41', 'A2'): sample}, 1620) for name in names], language=language)
                preview = preview_freezer_workbook(output)
                self.assertTrue(preview.can_import, preview.issues)
                self.assertEqual([name for name, _ in preview.freezers], names)
                self.assertEqual(preview.record_count, 4)
                if expected is None:
                    expected = preview.freezers
                self.assertEqual(preview.freezers, expected)
                self.assertEqual(preview.freezers[0][1]['41/A2'], sample.as_dict())
                with zipfile.ZipFile(output) as book:
                    rows = excel._sheet_rows(book, 'xl/worksheets/sheet2.xml', excel._shared_strings(book))
                    self.assertEqual(tuple(rows[0].values()), tuple(excel._excel_text(h, language) for h in excel.TUBE_HEADERS))
                    instructions = book.read('xl/worksheets/sheet6.xml').decode('utf-8')
                    if language == 'en':
                        self.assertIn('Cryotube Inventory - Instructions', instructions)
                        self.assertNotRegex(instructions, r'[\u4e00-\u9fff]')
                repository = FreezerRepository(root / f'import-{language}.db', root / 'missing.json')
                repository.import_freezers(preview.freezers, overwrite=True)
                self.assertEqual(sum(s.occupied for s in repository.box_samples.values()), 4)
                self.assertTrue(list(repository.backup_dir.glob('*.db')))

    def test_english_empty_and_legacy_workbooks_are_importable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            empty = root / 'empty.xlsx'
            export_cryotube_workbook(empty, [('Empty Tank', {}, 1620)], language='en')
            preview = preview_freezer_workbook(empty)
            self.assertTrue(preview.can_import, preview.issues)
            self.assertEqual(preview.record_count, 0)
            legacy = root / 'legacy.xlsx'
            record = UnitRecord(sample_name='HEK293T', stored_date='2026-08-19', stored_by='Horace', notes='备注')
            export_freezer_workbook(legacy, [('Legacy', {'11': record}, 4, 5)], language='en')
            preview = preview_freezer_workbook(legacy)
            self.assertTrue(preview.can_import, preview.issues)
            self.assertEqual(preview.freezers[0][1]['11']['notes'], '备注')
            with zipfile.ZipFile(legacy) as book:
                summary = book.read('xl/worksheets/sheet1.xml').decode('utf-8')
                self.assertIn('"Occupied"', summary)
                self.assertNotIn('已占用', summary)
                self.assertIn('Occupied', book.read('xl/worksheets/sheet2.xml').decode('utf-8'))

    def test_mixed_case_headers_and_duplicate_aliases_are_validated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample = BoxSample(sample_name='A549', stored_date='2026-08-19', stored_by='Horace')
            original = root / 'original.xlsx'
            export_cryotube_workbook(original, [('Tank', {('11', 'A1'): sample}, 81)], language='en')
            with zipfile.ZipFile(original) as book:
                files = {name: book.read(name) for name in book.namelist()}
            key = 'xl/worksheets/sheet2.xml'
            sheet = files[key].decode('utf-8').replace('Box Slot', '  bOx SlOt  ').replace('Cell Name', '细胞名称')
            mixed = root / 'mixed.xlsx'
            with zipfile.ZipFile(mixed, 'w') as book:
                for name, contents in files.items():
                    book.writestr(name, sheet if name == key else contents)
            self.assertTrue(preview_freezer_workbook(mixed).can_import)
            duplicate = root / 'duplicate.xlsx'
            sheet = sheet.replace('Full Location', 'cell name')
            with zipfile.ZipFile(duplicate, 'w') as book:
                for name, contents in files.items():
                    book.writestr(name, sheet if name == key else contents)
            preview = preview_freezer_workbook(duplicate)
            self.assertFalse(preview.can_import)
            self.assertIn('重复', preview.issues[0].message)
            missing = root / 'missing.xlsx'
            sheet = files[key].decode('utf-8').replace('>Horace<', '><')
            with zipfile.ZipFile(missing, 'w') as book:
                for name, contents in files.items():
                    book.writestr(name, sheet if name == key else contents)
            preview = preview_freezer_workbook(missing)
            self.assertFalse(preview.can_import)
            self.assertEqual(preview.issues[0].field, '入库人')

    def test_bilingual_event_register_keeps_notes_and_only_stock_in_out(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / 'events.db', root / 'missing.json')
            sample = BoxSample(sample_name='出库', stored_date='2026-08-19', stored_by='入库', notes='保留备注 & details')
            repository.batch_set_box_samples('11', ['A1'], sample)
            repository.move_box_sample('11', 'A1', 'A2')
            repository.batch_checkout_box_samples('11', ['A2'], '操作人', '2026-08-20')
            before = repository.path.read_bytes()
            for language in ('zh', 'en'):
                output = root / f'events-{language}.xlsx'
                export_inventory_events_workbook(output, repository.inventory_events, language=language)
                with zipfile.ZipFile(output) as book:
                    rows = excel._sheet_rows(book, 'xl/worksheets/sheet1.xml', excel._shared_strings(book))
                    headers = rows[1]
                    self.assertEqual(tuple(headers.values()), tuple(excel._excel_text(h, language) for h in excel.EVENT_HEADERS))
                    data = rows[2:]
                    self.assertEqual(len(data), 2)
                    self.assertEqual({r[5] for r in data}, {'入库', '出库'} if language == 'zh' else {'Stock In', 'Stock Out'})
                    self.assertTrue(all(r[2] == '出库' and r[9] == sample.notes for r in data))
                    self.assertEqual({r[7] for r in data}, {'入库', '操作人'})
                    self.assertTrue(all(r[6] == 1 for r in data))
                    self.assertEqual({excel._import_text(r[1], as_date=True) for r in data}, {'2026-08-19', '2026-08-20'})
                    self.assertTrue(all(excel._import_text(r[3], as_date=True) == '2026-08-19' for r in data))
                    self.assertEqual(len(ET.fromstring(book.read('xl/workbook.xml')).findall('x:sheets/x:sheet', excel.NS)), 1)
                with self.assertRaises(ValueError):
                    preview_freezer_workbook(output)  # A register is not an inventory template.
            self.assertEqual(repository.path.read_bytes(), before)

    def test_old_database_defaults_to_four_columns_and_five_layers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "legacy.db"
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute(
                    """
                    CREATE TABLE freezers (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                        archived INTEGER NOT NULL DEFAULT 0,
                        storage_columns INTEGER NOT NULL DEFAULT 4,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO freezers VALUES(?,?,?,?,?)",
                    ("freezer-1", "液氮罐 1", 0, 4, "2026-08-01T00:00:00"),
                )

            repository = FreezerRepository(database, root / "missing.json")
            self.assertEqual(repository.storage_columns, 4)
            self.assertEqual(repository.storage_layers, 5)
            self.assertEqual(repository.storage_capacity, 20)
            with closing(sqlite3.connect(database)) as connection:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(freezers)")}
            self.assertIn("storage_layers", columns)

    def test_storage_spec_resize_is_safe_and_keeps_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            repository.configure_storage(7, 6)
            self.assertEqual(repository.storage_capacity, 42)
            self.assertEqual(len(all_unit_codes(7, 6)), 42)
            self.assertTrue(repository.is_storage_code("76"))
            repository.configure_box("65", 8, 8)
            repository.set_box_sample(
                "66",
                "A1",
                BoxSample(
                    sample_name="K562",
                    stored_date="2026-08-17",
                    stored_by="Horace",
                ),
            )
            event_count = repository.count_inventory_events()

            self.assertEqual(repository.storage_resize_conflicts(5, 5), ["66"])
            with self.assertRaisesRegex(ValueError, "66"):
                repository.configure_storage(5, 5)
            self.assertEqual((repository.storage_columns, repository.storage_layers), (7, 6))
            self.assertTrue(repository.get_box_sample("66", "A1").occupied)
            self.assertEqual(repository.count_inventory_events(), event_count)

            repository.move_boxes(["66"], repository.current_freezer_id, ["11"])
            repository.configure_storage(5, 5)
            self.assertEqual((repository.storage_columns, repository.storage_layers), (5, 5))
            self.assertFalse(repository.is_storage_code("66"))
            self.assertTrue(repository.get_box_sample("11", "A1").occupied)
            self.assertNotIn((repository.current_freezer_id, "65"), repository.box_layouts)
            self.assertEqual(repository.count_inventory_events(), event_count)
            self.assertTrue(any(event.unit_code == "66" for event in repository.inventory_events))
            reloaded = FreezerRepository(root / "storage.db", root / "missing.json")
            self.assertEqual((reloaded.storage_columns, reloaded.storage_layers), (5, 5))
            self.assertEqual(reloaded.get_box_sample("11", "A1").sample_name, "K562")

    def test_storage_spec_detects_column_and_layer_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            repository.configure_storage(6, 6)
            sample = BoxSample(
                sample_name="A549", stored_date="2026-08-17", stored_by="Horace"
            )
            repository.set_box_sample("61", "A1", sample)
            repository.set_box_sample("16", "A1", sample)
            repository.set("62", UnitRecord(sample_name="历史样品"))
            self.assertTrue(repository.box_has_samples("62"))
            self.assertEqual(repository.storage_resize_conflicts(5, 6), ["61", "62"])
            self.assertEqual(repository.storage_resize_conflicts(6, 5), ["16"])
            self.assertEqual(repository.storage_resize_conflicts(5, 5), ["16", "61", "62"])
            with self.assertRaises(ValueError):
                repository.configure_storage(10, 5)

    def test_excel_import_infers_new_tank_spec_and_rejects_existing_overflow(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sample = BoxSample(sample_name="CHO-K1", stored_by="Horace")
            workbook = root / "wide-tank.xlsx"
            export_cryotube_workbook(workbook, [("液氮罐 2", {("76", "A1"): sample}, 81)])
            preview = preview_freezer_workbook(workbook)
            self.assertEqual(preview.error_count, 0)

            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            repository.import_freezers(preview.freezers, overwrite=True)
            imported = next(
                freezer for freezer in repository.freezers.values() if freezer.name == "液氮罐 2"
            )
            self.assertEqual((imported.storage_columns, imported.storage_layers), (7, 6))

            repository.switch_freezer(next(
                freezer_id
                for freezer_id, freezer in repository.freezers.items()
                if freezer.name == "液氮罐 1"
            ))
            repository.set_box_sample(
                "11",
                "A1",
                BoxSample(sample_name="原有库存", stored_date="2026-08-17", stored_by="Horace"),
            )
            existing_overflow = [("液氮罐 1", preview.freezers[0][1])]
            with self.assertRaisesRegex(ValueError, "请先调整液氮罐规格"):
                repository.import_freezers(existing_overflow, overwrite=True)
            current = next(
                freezer for freezer in repository.freezers.values() if freezer.name == "液氮罐 1"
            )
            self.assertEqual((current.storage_columns, current.storage_layers), (4, 5))
            self.assertEqual(repository.get_box_sample("11", "A1").sample_name, "原有库存")

    def test_basic_cell_search_can_cover_all_active_freezers(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            first_freezer_id = repository.current_freezer_id
            sample = BoxSample(
                sample_name="K562",
                stored_date="2026-08-17",
                stored_by="Horace",
            )
            repository.set_box_sample("11", "A1", sample)
            second_freezer_id = repository.add_freezer("液氮罐 2")
            repository.set_box_sample("21", "B2", sample)

            current_only = repository.search_box_samples("K562")
            self.assertEqual({item[0] for item in current_only}, {second_freezer_id})
            all_freezers = repository.search_box_samples("K562", all_freezers=True)
            self.assertEqual({item[0] for item in all_freezers}, {first_freezer_id, second_freezer_id})

            repository.switch_freezer(first_freezer_id)
            repository.archive_freezer(second_freezer_id)
            active_results = repository.search_box_samples("K562", all_freezers=True)
            self.assertEqual({item[0] for item in active_results}, {first_freezer_id})

    def test_large_event_history_uses_database_paging_without_rewriting_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "storage.db"
            repository = FreezerRepository(database, root / "missing.json")
            event_fields = tuple(InventoryEvent.__dataclass_fields__)
            columns = ",".join(event_fields)
            placeholders = ",".join("?" for _ in event_fields)
            actions = ("IN", "OUT", "CLEAR")
            events = []
            for index in range(5000):
                action = actions[index % len(actions)]
                event = InventoryEvent(
                    event_id=f"scale-{index:06d}",
                    batch_id=f"batch-{index // 10:05d}",
                    action=action,
                    freezer_id=repository.current_freezer_id,
                    freezer_name="液氮罐 1",
                    unit_code="11",
                    position=f"A{index % 9 + 1}",
                    sample_name=f"Cell-{index % 25:02d}",
                    operator="Horace" if action in {"IN", "OUT"} else "",
                    operation_date="2026-08-14",
                    frozen_date="2026-08-01",
                    created_at=f"2026-08-14T12:00:00.{index:06d}",
                )
                events.append(tuple(getattr(event, field) for field in event_fields))
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.executemany(
                    f"INSERT INTO inventory_events({columns}) VALUES({placeholders})",
                    events,
                )

            reloaded = FreezerRepository(database, root / "missing.json")
            self.assertEqual(len(reloaded.inventory_events), EVENT_MEMORY_CACHE_LIMIT)
            self.assertEqual(reloaded.count_inventory_events(), 5000)
            self.assertEqual(reloaded.count_inventory_events(actions={"IN", "OUT"}), 3334)
            self.assertEqual(len(reloaded.list_inventory_events(limit=300)), 300)
            self.assertEqual(reloaded.list_inventory_events(query="Cell-07", limit=1)[0].sample_name, "Cell-07")

            reloaded.set_box_sample(
                "11",
                "B1",
                BoxSample(
                    sample_name="K562",
                    stored_date="2026-08-17",
                    stored_by="Horace",
                ),
            )
            self.assertEqual(reloaded.count_inventory_events(), 5001)
            self.assertEqual(len(reloaded.inventory_events), EVENT_MEMORY_CACHE_LIMIT)

    def test_repository_requires_person_and_normalizes_one_tube(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            with self.assertRaisesRegex(ValueError, "入库人不能为空"):
                repository.set_box_sample("11", "A1", BoxSample(sample_name="K562"))
            repository.set_box_sample(
                "11",
                "A1",
                BoxSample(
                    sample_name="K562",
                    quantity="8",
                    sample_id="SHOULD-NOT-BE-STORED",
                    stored_date="2026-08-14",
                    stored_by="Horace",
                ),
            )
            saved = repository.get_box_sample("11", "A1")
            self.assertEqual(saved.quantity, "1")
            self.assertEqual(saved.sample_id, "")

    def test_batch_in_out_clear_and_move_create_atomic_events(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            sample = BoxSample(
                sample_name="A549",
                sample_type="人肺癌细胞",
                stored_date="2026-08-14",
                stored_by="Horace",
                notes="P8；DMEM 培养",
            )
            self.assertEqual(repository.batch_set_box_samples("11", ["A1", "A2"], sample), 2)
            inbound = [event for event in repository.inventory_events if event.action == "IN"]
            self.assertEqual(len(inbound), 2)
            self.assertEqual(len({event.batch_id for event in inbound}), 1)

            event_count = len(repository.inventory_events)
            with self.assertRaisesRegex(ValueError, "已有细胞"):
                repository.batch_set_box_samples("11", ["A2", "A3"], sample)
            self.assertFalse(repository.get_box_sample("11", "A3").occupied)
            self.assertEqual(len(repository.inventory_events), event_count)

            self.assertEqual(repository.batch_checkout_box_samples("11", ["A1", "A2"], "李明", "2026-08-14"), 2)
            outbound = [event for event in repository.inventory_events if event.action == "OUT"]
            self.assertEqual(len(outbound), 2)
            self.assertEqual(len({event.batch_id for event in outbound}), 1)
            self.assertTrue(all(event.frozen_date == "2026-08-14" for event in outbound))
            self.assertTrue(all(event.notes == "P8；DMEM 培养" for event in outbound))
            self.assertFalse(repository.get_box_sample("11", "A1").occupied)

            repository.batch_set_box_samples("11", ["B1"], sample)
            repository.move_box_sample("11", "B1", "B2")
            repository.move_box_sample("11", "B2", "B1", undo=True)
            self.assertEqual(repository.inventory_events[-2].action, "IN_MOVE")
            self.assertEqual(repository.inventory_events[-1].action, "IN_MOVE_UNDO")
            self.assertEqual(repository.inventory_events[-2].operator, "")
            self.assertEqual(repository.inventory_events[-1].operator, "")
            self.assertEqual(repository.inventory_events[-2].operation_date, date.today().isoformat())
            self.assertEqual(repository.inventory_events[-2].frozen_date, "2026-08-14")
            self.assertIn("P8；DMEM 培养", repository.inventory_events[-2].notes)
            selected_actions = repository.list_inventory_events(actions={"IN", "OUT"})
            self.assertTrue(selected_actions)
            self.assertTrue(all(event.action in {"IN", "OUT"} for event in selected_actions))
            self.assertEqual(repository.list_inventory_events(actions=set()), [])

            with self.assertRaisesRegex(ValueError, "有效日期"):
                repository.batch_checkout_box_samples("11", ["B1"], "李明", "2026-99-99")
            self.assertTrue(repository.get_box_sample("11", "B1").occupied)

            repository.batch_clear_box_samples("11", ["B1"])
            self.assertEqual(repository.inventory_events[-1].action, "CLEAR")
            self.assertFalse(repository.get_box_sample("11", "B1").occupied)

    def test_inventory_and_event_exports_are_separate(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            repository = FreezerRepository(root / "storage.db", root / "missing.json")
            sample = BoxSample(
                sample_name="K562",
                stored_date="2026-08-14",
                stored_by="Horace",
                notes="正式登记备注",
            )
            repository.batch_set_box_samples("11", ["A1"], sample)
            workbook = root / "inventory.xlsx"
            export_cryotube_workbook(
                workbook,
                [("液氮罐 1", {("11", "A1"): sample}, 1620)],
            )
            preview = preview_freezer_workbook(workbook)
            self.assertEqual(preview.error_count, 0)
            self.assertEqual(preview.record_count, 1)
            with zipfile.ZipFile(workbook) as book:
                workbook_xml = book.read("xl/workbook.xml").decode("utf-8")
            self.assertNotIn("出入库登记", workbook_xml)

            repository.move_box_sample("11", "A1", "A2")
            repository.batch_checkout_box_samples("11", ["A2"], "Horace", "2026-08-14")
            repository.batch_set_box_samples("11", ["B1"], sample)
            repository.batch_clear_box_samples("11", ["B1"])
            event_workbook = root / "events.xlsx"
            export_inventory_events_workbook(event_workbook, repository.inventory_events)
            with zipfile.ZipFile(event_workbook) as book:
                event_workbook_xml = book.read("xl/workbook.xml").decode("utf-8")
                event_xml = book.read("xl/worksheets/sheet1.xml").decode("utf-8")
            self.assertIn("出入库登记", event_workbook_xml)
            for header in (
                "序号", "操作日期", "细胞名称", "冻存日期", "位置",
                "出入库", "数量（管）", "操作人", "签名", "备注",
            ):
                self.assertIn(header, event_xml)
            self.assertNotIn("细胞状态", event_xml)
            self.assertNotIn("操作批次", event_xml)
            self.assertNotIn("快捷移动", event_xml)
            self.assertNotIn("清除", event_xml)
            self.assertNotIn("历史迁入", event_xml)
            self.assertIn("正式登记备注", event_xml)

    def test_existing_inventory_is_backfilled_once_as_history(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            database = root / "storage.db"
            repository = FreezerRepository(database, root / "missing.json")
            repository.set_box_sample(
                "11",
                "A1",
                BoxSample(sample_name="CHO-K1", stored_date="2026-08-14", stored_by="Horace"),
            )
            with closing(sqlite3.connect(database)) as connection, connection:
                connection.execute("DELETE FROM inventory_events")
            reloaded = FreezerRepository(database, root / "missing.json")
            self.assertEqual(len(reloaded.inventory_events), 1)
            self.assertEqual(reloaded.inventory_events[0].action, "HISTORY")
            self.assertEqual(reloaded.inventory_events[0].operator, "")
            loaded_again = FreezerRepository(database, root / "missing.json")
            self.assertEqual(len(loaded_again.inventory_events), 1)

    def test_current_cryotube_export_can_roundtrip_supported_fields(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            workbook = root / "tubes.xlsx"
            sample = BoxSample(
                sample_name="HEK293T",
                quantity="2",
                sample_id="CELL-2026-001",
                sample_type="贴壁细胞",
                stored_date="2026-08-14",
                stored_by="Horace",
                notes="第3代",
            )
            export_cryotube_workbook(workbook, [("液氮罐 1", {("41", "A2"): sample}, 1620)])

            preview = preview_freezer_workbook(workbook)
            self.assertEqual(preview.error_count, 0)
            self.assertEqual(preview.record_count, 1)
            expected = sample.as_dict()
            expected["quantity"] = "1"
            expected["sample_id"] = ""
            self.assertEqual(preview.freezers[0][1]["41/A2"], expected)

            repository = FreezerRepository(root / "roundtrip.db", root / "missing.json")
            stats = repository.import_freezers(preview.freezers, overwrite=True)
            self.assertEqual(stats["records"], 1)
            self.assertFalse(any(event.action == "IMPORT" for event in repository.inventory_events))
            freezer_id = next(
                freezer_id
                for freezer_id, freezer in repository.freezers.items()
                if freezer.name == "液氮罐 1"
            )
            imported = repository.box_samples[(freezer_id, "41", "A2")]
            self.assertEqual(imported.as_dict(), expected)

            with zipfile.ZipFile(workbook) as book:
                ledger = book.read("xl/worksheets/sheet2.xml").decode("utf-8")
            self.assertNotIn("数量（管）", ledger)
            self.assertNotIn("细胞编号", ledger)

    def test_current_import_requires_stored_by(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            valid = Path(folder) / "valid_person.xlsx"
            workbook = Path(folder) / "missing_person.xlsx"
            export_cryotube_workbook(
                valid,
                [("液氮罐 1", {("11", "A1"): BoxSample(sample_name="K562", stored_by="Horace")}, 1620)],
            )
            with zipfile.ZipFile(valid) as source, zipfile.ZipFile(workbook, "w", zipfile.ZIP_DEFLATED) as target:
                for member in source.infolist():
                    content = source.read(member.filename)
                    if member.filename == "xl/worksheets/sheet2.xml":
                        text = content.decode("utf-8")
                        text = re.sub(r'<c r="I2"[^>]*>.*?</c>', "", text, count=1)
                        content = text.encode("utf-8")
                    target.writestr(member, content)
            preview = preview_freezer_workbook(workbook)
            self.assertEqual(preview.error_count, 1)
            self.assertEqual(preview.issues[0].field, "入库人")

    def test_current_import_rejects_position_without_cell_name(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            valid = root / "valid.xlsx"
            invalid = root / "missing_name.xlsx"
            export_cryotube_workbook(
                valid,
                [("液氮罐 1", {("11", "B3"): BoxSample(sample_name="K562")}, 1620)],
            )
            with zipfile.ZipFile(valid) as source, zipfile.ZipFile(invalid, "w", zipfile.ZIP_DEFLATED) as target:
                for member in source.infolist():
                    content = source.read(member.filename)
                    if member.filename == "xl/worksheets/sheet2.xml":
                        text = content.decode("utf-8")
                        text = re.sub(r'<c r="F2"[^>]*>.*?</c>', "", text, count=1)
                        content = text.encode("utf-8")
                    target.writestr(member, content)

            preview = preview_freezer_workbook(invalid)
            self.assertEqual(preview.error_count, 1)
            self.assertEqual(preview.issues[0].field, "细胞名称")

    def test_empty_current_workbook_is_valid(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workbook = Path(folder) / "empty.xlsx"
            export_cryotube_workbook(workbook, [("液氮罐 3", {}, 1620)])
            preview = preview_freezer_workbook(workbook)
            self.assertEqual(preview.error_count, 0)
            self.assertEqual(preview.record_count, 0)
            self.assertEqual(preview.freezers, [("液氮罐 3", {})])

    def test_legacy_box_workbook_remains_importable(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workbook = Path(folder) / "legacy.xlsx"
            record = UnitRecord(
                sample_name="CHO-K1",
                stored_date="2026-08-01",
                experiment_id="EXP-001",
                sample_type="贴壁细胞",
                stored_by="Horace",
            )
            export_freezer_workbook(workbook, [("液氮罐 1", {"11": record}, 4)])
            preview = preview_freezer_workbook(workbook)
            self.assertEqual(preview.error_count, 0)
            self.assertEqual(preview.record_count, 1)
            self.assertEqual(preview.freezers[0][1]["11"]["sample_name"], "CHO-K1")

            configurable = Path(folder) / "configurable.xlsx"
            export_freezer_workbook(configurable, [("液氮罐 2", {"76": record}, 7, 6)])
            configurable_preview = preview_freezer_workbook(configurable)
            self.assertEqual(configurable_preview.error_count, 0)
            self.assertEqual(configurable_preview.freezers[0][1]["76"]["sample_name"], "CHO-K1")


if __name__ == "__main__":
    unittest.main()
