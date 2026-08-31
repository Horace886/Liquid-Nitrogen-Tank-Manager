from pathlib import Path
import sys
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liquid_nitrogen_tank_excel import export_cryotube_workbook, export_inventory_events_workbook, preview_freezer_workbook
from liquid_nitrogen_tank_store import BoxSample, FreezerRepository


with TemporaryDirectory() as folder:
    repository = FreezerRepository(Path(folder) / "storage.db", Path(folder) / "missing.json")
    sample = BoxSample(
        sample_name="A549",
        sample_type="人肺癌细胞",
        stored_date="2026-08-01",
        stored_by="Horace",
        notes="P8；DMEM 培养",
    )
    repository.batch_set_box_samples("11", ["A1", "A2"], sample)
    repository.move_box_sample("11", "A1", "B1")
    repository.move_box_sample("11", "B1", "A1", undo=True)
    repository.batch_checkout_box_samples("11", ["A2"], "张蕾", "2026-08-14")
    inventory_output = Path(__file__).with_name("inventory_export.xlsx")
    event_output = Path(__file__).with_name("event_export.xlsx")
    samples = {("11", position): item for position, item in repository.get_box_samples("11").items()}
    export_cryotube_workbook(
        inventory_output,
        [("液氮罐 1", samples, 1620)],
    )
    export_inventory_events_workbook(event_output, repository.list_inventory_events())
    preview = preview_freezer_workbook(inventory_output)
    print(
        "quick_ledger_ok",
        f"events={len(repository.inventory_events)}",
        f"records={preview.record_count}",
        f"errors={preview.error_count}",
    )
