from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liquid_nitrogen_tank_manager import FreezerManagerApp
from liquid_nitrogen_tank_store import EVENT_MEMORY_CACHE_LIMIT, FreezerRepository


EVENT_TOTAL = 100_000

with TemporaryDirectory(ignore_cleanup_errors=True) as folder:
    root = Path(folder)
    database = root / "storage.db"
    repository = FreezerRepository(database, root / "missing.json")
    freezer_id = repository.current_freezer_id
    insert_sql = (
        "INSERT INTO inventory_events("
        "event_id,batch_id,action,freezer_id,freezer_name,unit_code,position,"
        "sample_name,operator,operation_date,frozen_date,created_at"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"
    )
    actions = ("IN", "OUT", "CLEAR", "IN_MOVE", "HISTORY")
    rows = (
        (
            f"perf-{index:06d}",
            f"batch-{index // 10:06d}",
            actions[index % len(actions)],
            freezer_id,
            "液氮罐 1",
            "11",
            f"{chr(65 + index % 9)}{index % 9 + 1}",
            f"Cell-{index % 100:03d}",
            "Horace" if actions[index % len(actions)] in {"IN", "OUT"} else "",
            "2026-08-17",
            "2026-08-01",
            f"2026-08-17T12:00:00.{index:06d}",
        )
        for index in range(EVENT_TOTAL)
    )
    with sqlite3.connect(database) as connection:
        connection.executemany(insert_sql, rows)
        connection.commit()

    started = time.perf_counter()
    repository = FreezerRepository(database, root / "missing.json")
    load_seconds = time.perf_counter() - started
    if len(repository.inventory_events) != EVENT_MEMORY_CACHE_LIMIT:
        raise RuntimeError(f"unexpected memory cache size: {len(repository.inventory_events)}")

    started = time.perf_counter()
    count = repository.count_inventory_events()
    page = repository.list_inventory_events(limit=300)
    query_seconds = time.perf_counter() - started
    if count != EVENT_TOTAL or len(page) != 300:
        raise RuntimeError(f"database paging failed: count={count}, page={len(page)}")

    app = FreezerManagerApp(repository)
    app.attributes("-alpha", 0.0)
    app.update()
    started = time.perf_counter()
    app.show_inventory_events_page()
    app.update_idletasks()
    page_seconds = time.perf_counter() - started
    app.destroy()

    print(
        "large_event_perf_ok",
        f"events={count}",
        f"cache={len(repository.inventory_events)}",
        f"load_seconds={load_seconds:.3f}",
        f"query_seconds={query_seconds:.3f}",
        f"page_seconds={page_seconds:.3f}",
    )
