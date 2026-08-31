from __future__ import annotations

import json
import sqlite3
import sys
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liquid_nitrogen_tank_excel import export_cryotube_workbook, preview_freezer_workbook  # noqa: E402
from liquid_nitrogen_tank_store import FreezerRepository, all_unit_codes  # noqa: E402


def main() -> None:
    database = Path(__file__).with_name("source_copy.db")
    repository = FreezerRepository(database, Path(__file__).with_name("missing.json"))
    payload = []
    for freezer_id, freezer in repository.freezers.items():
        if freezer.archived:
            continue
        samples = {
            (code, position): sample
            for (stored_id, code, position), sample in repository.box_samples.items()
            if stored_id == freezer_id and sample.occupied
        }
        capacity = sum(
            repository.get_box_layout(code, freezer_id).rows
            * repository.get_box_layout(code, freezer_id).columns
            for code in all_unit_codes(freezer.storage_columns)
        )
        payload.append((freezer.name, samples, capacity))
    workbook = Path(__file__).with_name("event_export.xlsx")
    export_cryotube_workbook(workbook, payload, repository.inventory_events)
    preview = preview_freezer_workbook(workbook)
    with zipfile.ZipFile(workbook) as book:
        sheet_count = sum(name.startswith("xl/worksheets/sheet") for name in book.namelist())
    with sqlite3.connect(database) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        stored_events = connection.execute("SELECT count(1) FROM inventory_events").fetchone()[0]
    print(json.dumps({
        "integrity": integrity,
        "tubes": sum(len(item[1]) for item in payload),
        "events": len(repository.inventory_events),
        "stored_events": stored_events,
        "history_events": sum(event.action == "HISTORY" for event in repository.inventory_events),
        "sheets": sheet_count,
        "preview_records": preview.record_count,
        "preview_errors": preview.error_count,
        "preview_warnings": preview.warning_count,
        "workbook": str(workbook),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
