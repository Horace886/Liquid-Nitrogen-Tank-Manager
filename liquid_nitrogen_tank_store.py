from __future__ import annotations

import copy
import json
import re
import shutil
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Sequence


DB_FILE = Path(__file__).with_name("liquid_nitrogen_tank_storage.db")
LEGACY_JSON_FILE = Path(__file__).with_name("liquid_nitrogen_tank_storage_data.json")
EVENT_MEMORY_CACHE_LIMIT = 1000
RECORD_FIELDS = (
    "sample_name",
    "stored_date",
    "experiment_id",
    "sample_type",
    "sample_count",
    "sample_date",
    "stored_by",
    "claimed_by",
    "claimed_date",
    "claimed_amount",
    "notes",
)

BOX_SAMPLE_FIELDS = (
    "sample_name",
    "quantity",
    "sample_id",
    "sample_type",
    "stored_date",
    "stored_by",
    "notes",
)


_GBK_PINYIN_INITIALS = (
    (-20319, "A"),
    (-20284, "B"),
    (-19776, "C"),
    (-19219, "D"),
    (-18711, "E"),
    (-18527, "F"),
    (-18240, "G"),
    (-17923, "H"),
    (-17418, "J"),
    (-16475, "K"),
    (-16213, "L"),
    (-15641, "M"),
    (-15166, "N"),
    (-14923, "O"),
    (-14915, "P"),
    (-14631, "Q"),
    (-14150, "R"),
    (-14091, "S"),
    (-13319, "T"),
    (-12839, "W"),
    (-12557, "X"),
    (-11848, "Y"),
    (-11056, "Z"),
)


def name_initial(value: str) -> str:
    """Return the first name character as an A-Z Latin/Pinyin initial."""
    text = value.strip()
    if not text:
        return ""
    first = text[0]
    if first.isascii():
        return first.upper()
    try:
        encoded = first.encode("gbk")
    except UnicodeEncodeError:
        return first.casefold()
    if len(encoded) != 2:
        return first.casefold()
    code = encoded[0] * 256 + encoded[1] - 65536
    initial = ""
    for boundary, letter in _GBK_PINYIN_INITIALS:
        if code < boundary:
            break
        initial = letter
    return initial or first.casefold()


def inventory_name_sort_key(value: str) -> tuple[int, str, str]:
    """Sort Latin names first, followed by Chinese names by Pinyin initial."""
    text = value.strip()
    latin_group = 0 if text and text[0].isascii() else 1
    return latin_group, name_initial(text), text.casefold()


def compartment_code(column: int, layer: int) -> str:
    """Return a liquid-nitrogen tank box-position code: column + layer."""
    return f"{column}{layer}"


def unit_code(column: int, layer: int) -> str:
    return compartment_code(column, layer)


def parse_unit_code(code: str) -> tuple[int, int] | None:
    if not re.fullmatch(r"[1-9][1-9]", code):
        return None
    return tuple(int(char) for char in code)  # type: ignore[return-value]


def all_unit_codes(columns: int = 4, layers: int = 5) -> list[str]:
    return [
        unit_code(column, layer)
        for column in range(1, columns + 1)
        for layer in range(1, layers + 1)
    ]


@dataclass
class UnitRecord:
    sample_name: str = ""
    stored_date: str = ""
    experiment_id: str = ""
    sample_type: str = ""
    sample_count: str = ""
    sample_date: str = ""
    stored_by: str = ""
    claimed_by: str = ""
    claimed_date: str = ""
    claimed_amount: str = ""
    notes: str = ""

    @property
    def occupied(self) -> bool:
        return bool(self.sample_name.strip() or self.experiment_id.strip())

    @classmethod
    def from_dict(cls, value: object) -> "UnitRecord":
        if not isinstance(value, dict):
            return cls()
        record = cls(**{key: str(value.get(key, "")) for key in RECORD_FIELDS})
        if not record.sample_name and record.experiment_id:
            record.sample_name = record.experiment_id
        if not record.stored_date and record.sample_date:
            record.stored_date = record.sample_date
        return record

    def as_dict(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in RECORD_FIELDS}


@dataclass
class BoxLayout:
    rows: int = 9
    columns: int = 9
    name: str = ""


@dataclass
class BoxSample:
    sample_name: str = ""
    quantity: str = "1"
    sample_id: str = ""
    sample_type: str = ""
    stored_date: str = ""
    stored_by: str = ""
    notes: str = ""

    @property
    def occupied(self) -> bool:
        return bool(self.sample_name.strip())

    @classmethod
    def from_dict(cls, value: object) -> "BoxSample":
        if not isinstance(value, dict):
            return cls()
        normalized = {key: str(value.get(key, "")) for key in BOX_SAMPLE_FIELDS}
        # One occupied hole always represents exactly one cryotube.  Keep the
        # legacy database columns readable, but stop carrying their old values
        # into the current data model.
        normalized["quantity"] = "1"
        normalized["sample_id"] = ""
        return cls(**normalized)

    def as_dict(self) -> dict[str, str]:
        return {key: str(getattr(self, key)) for key in BOX_SAMPLE_FIELDS}


@dataclass
class FreezerData:
    name: str
    records: dict[str, UnitRecord]
    archived: bool = False
    storage_columns: int = 4
    storage_layers: int = 5


@dataclass
class InventoryEvent:
    event_id: str
    batch_id: str
    action: str
    freezer_id: str
    freezer_name: str
    unit_code: str
    position: str
    target_unit_code: str = ""
    target_position: str = ""
    sample_name: str = ""
    sample_type: str = ""
    operator: str = ""
    operation_date: str = ""
    frozen_date: str = ""
    notes: str = ""
    created_at: str = ""


class FreezerRepository:
    """SQLite-backed repository with automatic migration from the former JSON format."""

    def __init__(self, path: Path | None = None, legacy_json_path: Path | None = None):
        if path is None:
            self.path = DB_FILE
            self.legacy_json_path = legacy_json_path or LEGACY_JSON_FILE
        elif Path(path).suffix.lower() == ".json":
            self.path = Path(path).with_suffix(".db")
            self.legacy_json_path = Path(path)
        else:
            self.path = Path(path)
            self.legacy_json_path = legacy_json_path or self.path.with_suffix(".json")
        self.backup_dir = self.path.with_name("backups")
        self.freezers: dict[str, FreezerData] = {}
        self.box_layouts: dict[tuple[str, str], BoxLayout] = {}
        self.box_samples: dict[tuple[str, str, str], BoxSample] = {}
        self.inventory_events: list[InventoryEvent] = []
        self.inventory_alerts: dict[str, tuple[str, int, int]] = {}
        self.default_inventory_alert: tuple[int, int] = (3, 5)
        self.current_freezer_id = ""
        self.last_import_backup: Path | None = None
        self.migration_backup: Path | None = None
        self.load()

    def _connect(self, path: Path | None = None) -> sqlite3.Connection:
        connection = sqlite3.connect(path or self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS freezers (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                archived INTEGER NOT NULL DEFAULT 0,
                storage_columns INTEGER NOT NULL DEFAULT 4,
                storage_layers INTEGER NOT NULL DEFAULT 5,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS unit_records (
                freezer_id TEXT NOT NULL REFERENCES freezers(id) ON DELETE CASCADE,
                code TEXT NOT NULL,
                sample_name TEXT NOT NULL DEFAULT '',
                stored_date TEXT NOT NULL DEFAULT '',
                experiment_id TEXT NOT NULL,
                sample_type TEXT NOT NULL DEFAULT '',
                sample_count TEXT NOT NULL DEFAULT '',
                sample_date TEXT NOT NULL DEFAULT '',
                stored_by TEXT NOT NULL DEFAULT '',
                claimed_by TEXT NOT NULL DEFAULT '',
                claimed_date TEXT NOT NULL DEFAULT '',
                claimed_amount TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (freezer_id, code)
            );
            CREATE INDEX IF NOT EXISTS idx_records_experiment ON unit_records(experiment_id);
            CREATE INDEX IF NOT EXISTS idx_records_sample_type ON unit_records(sample_type);
            CREATE INDEX IF NOT EXISTS idx_records_stored_by ON unit_records(stored_by);
            CREATE TABLE IF NOT EXISTS box_layouts (
                freezer_id TEXT NOT NULL REFERENCES freezers(id) ON DELETE CASCADE,
                unit_code TEXT NOT NULL,
                rows INTEGER NOT NULL DEFAULT 9,
                columns_count INTEGER NOT NULL DEFAULT 9,
                name TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (freezer_id, unit_code)
            );
            CREATE TABLE IF NOT EXISTS box_samples (
                freezer_id TEXT NOT NULL REFERENCES freezers(id) ON DELETE CASCADE,
                unit_code TEXT NOT NULL,
                position TEXT NOT NULL,
                row_index INTEGER NOT NULL,
                column_index INTEGER NOT NULL,
                sample_name TEXT NOT NULL DEFAULT '',
                quantity TEXT NOT NULL DEFAULT '1',
                sample_id TEXT NOT NULL DEFAULT '',
                sample_type TEXT NOT NULL DEFAULT '',
                stored_date TEXT NOT NULL DEFAULT '',
                stored_by TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (freezer_id, unit_code, position)
            );
            CREATE INDEX IF NOT EXISTS idx_box_samples_name ON box_samples(sample_name);
            CREATE TABLE IF NOT EXISTS inventory_alerts (
                sample_key TEXT PRIMARY KEY,
                sample_name TEXT NOT NULL,
                low_threshold INTEGER NOT NULL DEFAULT 3,
                warning_threshold INTEGER NOT NULL DEFAULT 5
            );
            CREATE TABLE IF NOT EXISTS inventory_events (
                event_id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                freezer_id TEXT NOT NULL,
                freezer_name TEXT NOT NULL DEFAULT '',
                unit_code TEXT NOT NULL DEFAULT '',
                position TEXT NOT NULL DEFAULT '',
                target_unit_code TEXT NOT NULL DEFAULT '',
                target_position TEXT NOT NULL DEFAULT '',
                sample_name TEXT NOT NULL DEFAULT '',
                sample_type TEXT NOT NULL DEFAULT '',
                operator TEXT NOT NULL DEFAULT '',
                operation_date TEXT NOT NULL DEFAULT '',
                frozen_date TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_inventory_events_created ON inventory_events(created_at);
            CREATE INDEX IF NOT EXISTS idx_inventory_events_sample ON inventory_events(sample_name);
            CREATE INDEX IF NOT EXISTS idx_inventory_events_action ON inventory_events(action);
            CREATE INDEX IF NOT EXISTS idx_inventory_events_date ON inventory_events(operation_date);
            CREATE INDEX IF NOT EXISTS idx_inventory_events_action_date ON inventory_events(action, operation_date);
            CREATE INDEX IF NOT EXISTS idx_inventory_events_freezer_date ON inventory_events(freezer_id, operation_date);
            """
        )
        existing_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(unit_records)")
        }
        for column in ("sample_name", "stored_date"):
            if column not in existing_columns:
                connection.execute(
                    f"ALTER TABLE unit_records ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_records_sample_name ON unit_records(sample_name)"
        )
        freezer_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(freezers)")
        }
        if "storage_columns" not in freezer_columns:
            connection.execute(
                "ALTER TABLE freezers ADD COLUMN storage_columns INTEGER NOT NULL DEFAULT 4"
            )
        if "storage_layers" not in freezer_columns:
            connection.execute(
                "ALTER TABLE freezers ADD COLUMN storage_layers INTEGER NOT NULL DEFAULT 5"
            )
        event_columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(inventory_events)")
        }
        if "frozen_date" not in event_columns:
            connection.execute(
                "ALTER TABLE inventory_events ADD COLUMN frozen_date TEXT NOT NULL DEFAULT ''"
            )
        migration_row = connection.execute(
            "SELECT value FROM settings WHERE key='inventory_events_migration_version'"
        ).fetchone()
        if migration_row is None or str(migration_row[0]) != "1":
            connection.execute(
                """
                UPDATE inventory_events
                SET frozen_date = operation_date
                WHERE frozen_date = ''
                  AND action IN ('IN', 'IN_MOVE', 'IN_MOVE_UNDO', 'ADJUST', 'HISTORY')
                """
            )
            connection.execute(
                """
                UPDATE inventory_events AS current
                SET frozen_date = COALESCE((
                    SELECT prior.frozen_date
                    FROM inventory_events AS prior
                    WHERE prior.freezer_id = current.freezer_id
                      AND prior.unit_code = current.unit_code
                      AND prior.position = current.position
                      AND prior.sample_name = current.sample_name
                      AND prior.frozen_date <> ''
                      AND prior.created_at <= current.created_at
                    ORDER BY prior.created_at DESC, prior.rowid DESC
                    LIMIT 1
                ), '')
                WHERE current.frozen_date = ''
                """
            )
            connection.execute(
                """
                UPDATE inventory_events AS current
                SET notes = COALESCE((
                    SELECT sample.notes
                    FROM box_samples AS sample
                    WHERE sample.freezer_id = current.freezer_id
                      AND sample.unit_code = current.unit_code
                      AND sample.position = current.position
                      AND sample.sample_name = current.sample_name
                      AND sample.notes <> ''
                    LIMIT 1
                ), '')
                WHERE current.notes = ''
                """
            )
            # Only formal inbound and outbound operations are named.
            connection.execute(
                "UPDATE inventory_events SET operator = '' WHERE action NOT IN ('IN', 'OUT')"
            )
            connection.execute(
                """
                UPDATE inventory_events AS current
                SET notes = COALESCE((
                    SELECT prior.notes
                    FROM inventory_events AS prior
                    WHERE prior.freezer_id = current.freezer_id
                      AND prior.unit_code = current.unit_code
                      AND prior.position = current.position
                      AND prior.sample_name = current.sample_name
                      AND prior.notes <> ''
                      AND prior.created_at <= current.created_at
                    ORDER BY prior.created_at DESC, prior.rowid DESC
                    LIMIT 1
                ), '')
                WHERE current.notes = ''
                """
            )
            connection.execute(
                "INSERT OR REPLACE INTO settings(key,value) "
                "VALUES('inventory_events_migration_version','1')"
            )

    def load(self) -> None:
        if not self.path.exists():
            if self.legacy_json_path.exists():
                self._migrate_json(self.legacy_json_path)
            else:
                self.freezers = {"freezer-1": FreezerData("液氮罐 1", {})}
                self.current_freezer_id = "freezer-1"
                self.save()
                return
        try:
            with closing(self._connect()) as connection, connection:
                self._create_schema(connection)
                loaded: dict[str, FreezerData] = {}
                for row in connection.execute(
                    "SELECT id, name, archived, storage_columns, storage_layers FROM freezers ORDER BY created_at, rowid"
                ):
                    name = str(row["name"])
                    match = re.fullmatch(r"-80°?C?\s*冰箱\s*(\d+)", name, re.IGNORECASE)
                    if match:
                        name = f"液氮罐 {match.group(1)}"
                    loaded[row["id"]] = FreezerData(
                        name,
                        {},
                        bool(row["archived"]),
                        max(1, min(9, int(row["storage_columns"]))),
                        max(1, min(9, int(row["storage_layers"]))),
                    )
                for row in connection.execute("SELECT * FROM unit_records ORDER BY code"):
                    freezer = loaded.get(row["freezer_id"])
                    if freezer is None or not parse_unit_code(row["code"]):
                        continue
                    freezer.records[row["code"]] = UnitRecord.from_dict(
                        {field: str(row[field]) for field in RECORD_FIELDS}
                    )
                requested = connection.execute(
                    "SELECT value FROM settings WHERE key='current_freezer_id'"
                ).fetchone()
                default_low_row = connection.execute(
                    "SELECT value FROM settings WHERE key='inventory_default_low'"
                ).fetchone()
                default_warning_row = connection.execute(
                    "SELECT value FROM settings WHERE key='inventory_default_warning'"
                ).fetchone()
                layouts = {
                    (str(row["freezer_id"]), str(row["unit_code"])): BoxLayout(
                        int(row["rows"]), int(row["columns_count"]), str(row["name"])
                    )
                    for row in connection.execute("SELECT * FROM box_layouts")
                    if str(row["freezer_id"]) in loaded and parse_unit_code(str(row["unit_code"]))
                }
                samples = {
                    (str(row["freezer_id"]), str(row["unit_code"]), str(row["position"])): BoxSample.from_dict(
                        {field: str(row[field]) for field in BOX_SAMPLE_FIELDS}
                    )
                    for row in connection.execute("SELECT * FROM box_samples")
                    if str(row["freezer_id"]) in loaded and parse_unit_code(str(row["unit_code"]))
                }
                alerts = {
                    str(row["sample_key"]): (
                        str(row["sample_name"]),
                        int(row["low_threshold"]),
                        int(row["warning_threshold"]),
                    )
                    for row in connection.execute("SELECT * FROM inventory_alerts")
                }
                recent_event_rows = list(connection.execute(
                    "SELECT * FROM inventory_events "
                    "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                    (EVENT_MEMORY_CACHE_LIMIT,),
                ))
                events = [
                    InventoryEvent(**{field: str(row[field]) for field in InventoryEvent.__dataclass_fields__})
                    for row in reversed(recent_event_rows)
                ]
            if not loaded:
                loaded = {"freezer-1": FreezerData("液氮罐 1", {})}
            active_ids = [key for key, value in loaded.items() if not value.archived]
            if not active_ids:
                new_id = f"freezer-{uuid.uuid4().hex[:10]}"
                loaded[new_id] = FreezerData("液氮罐 1", {})
                active_ids = [new_id]
            requested_id = str(requested["value"]) if requested else ""
            self.freezers = loaded
            self.box_layouts = layouts
            self.box_samples = samples
            self.inventory_alerts = alerts
            self.inventory_events = events
            try:
                default_low = int(default_low_row["value"]) if default_low_row else 3
                default_warning = int(default_warning_row["value"]) if default_warning_row else 5
                self.default_inventory_alert = (
                    default_low,
                    default_warning if default_warning >= default_low else default_low,
                )
            except (TypeError, ValueError):
                self.default_inventory_alert = (3, 5)
            self.current_freezer_id = (
                requested_id
                if requested_id in active_ids
                else active_ids[0]
            )
            if not self.inventory_events and self.box_samples:
                batch_id = self._new_batch_id("HIS")
                for (freezer_id, code, position), sample in sorted(self.box_samples.items()):
                    freezer = self.freezers.get(freezer_id)
                    if freezer is None or not sample.occupied:
                        continue
                    self._record_event(
                        "HISTORY",
                        freezer_id=freezer_id,
                        code=code,
                        position=position,
                        sample=sample,
                        operator=sample.stored_by,
                        operation_date=sample.stored_date,
                        batch_id=batch_id,
                    )
            self.save()
        except sqlite3.DatabaseError as exc:
            raise OSError(f"本地数据库无法读取：{exc}") from exc

    def _migrate_json(self, source: Path) -> None:
        try:
            raw_text = source.read_text(encoding="utf-8")
            raw = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as exc:
            raise OSError(f"旧版 JSON 数据无法迁移：{exc}") from exc

        def migrated_freezer(name: str, records: dict[str, UnitRecord]) -> FreezerData:
            coordinates = [parse_unit_code(code) for code in records]
            columns = max((item[0] for item in coordinates if item), default=4)
            layers = max((item[1] for item in coordinates if item), default=5)
            return FreezerData(name, records, storage_columns=max(4, columns), storage_layers=max(5, layers))

        loaded: dict[str, FreezerData] = {}
        raw_freezers = raw.get("freezers") if isinstance(raw, dict) else None
        if isinstance(raw_freezers, dict) and raw_freezers:
            for freezer_id, value in raw_freezers.items():
                if not isinstance(value, dict):
                    continue
                units = value.get("units", {})
                records = {
                    code: UnitRecord.from_dict(record)
                    for code, record in (units.items() if isinstance(units, dict) else [])
                    if parse_unit_code(code) and UnitRecord.from_dict(record).occupied
                }
                name = str(value.get("name", "")).strip() or f"液氮罐 {len(loaded) + 1}"
                loaded[str(freezer_id)] = migrated_freezer(name[:50], records)
            requested = str(raw.get("current_freezer_id", ""))
        else:
            items = raw.get("units", raw) if isinstance(raw, dict) else {}
            records = {
                code: UnitRecord.from_dict(record)
                for code, record in (items.items() if isinstance(items, dict) else [])
                if parse_unit_code(code) and UnitRecord.from_dict(record).occupied
            }
            loaded = {"freezer-1": migrated_freezer("液氮罐 1", records)}
            requested = "freezer-1"
        if not loaded:
            loaded = {"freezer-1": FreezerData("液氮罐 1", {})}
        self.freezers = loaded
        self.current_freezer_id = requested if requested in loaded else next(iter(loaded))
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = source.with_name(f"{source.stem}_SQLite迁移备份_{stamp}{source.suffix}")
        shutil.copy2(source, backup)
        self.migration_backup = backup
        self.save()

    @property
    def current_freezer(self) -> FreezerData:
        return self.freezers[self.current_freezer_id]

    @property
    def freezer_name(self) -> str:
        return self.current_freezer.name

    @property
    def storage_columns(self) -> int:
        return self.current_freezer.storage_columns

    @property
    def storage_layers(self) -> int:
        return self.current_freezer.storage_layers

    @property
    def storage_capacity(self) -> int:
        return self.storage_columns * self.storage_layers

    @property
    def tube_used_count(self) -> int:
        """Number of occupied cryobox positions (one position equals one cryotube)."""
        return sum(
            1
            for (freezer_id, code, _position), sample in self.box_samples.items()
            if freezer_id == self.current_freezer_id
            and self.is_storage_code(code)
            and sample.occupied
        )

    @property
    def tube_capacity(self) -> int:
        """Total cryotube capacity using each box's actual layout (default 9×9)."""
        return sum(
            self.get_box_layout(code).rows * self.get_box_layout(code).columns
            for code in all_unit_codes(self.storage_columns, self.storage_layers)
        )

    def is_storage_code(self, code: str, freezer_id: str | None = None) -> bool:
        parsed = parse_unit_code(code)
        target = self.freezers.get(freezer_id or self.current_freezer_id)
        return bool(
            parsed
            and target
            and parsed[0] <= target.storage_columns
            and parsed[1] <= target.storage_layers
        )

    def box_inventory_count(self, code: str, freezer_id: str | None = None) -> int:
        target_id = freezer_id or self.current_freezer_id
        sample_count = sum(
            stored_id == target_id and stored_code == code and sample.occupied
            for (stored_id, stored_code, _position), sample in self.box_samples.items()
        )
        legacy_count = int(self.freezers[target_id].records.get(code, UnitRecord()).occupied)
        return max(sample_count, legacy_count)

    def storage_resize_conflicts(self, columns: int, layers: int) -> list[str]:
        if not (1 <= columns <= 9 and 1 <= layers <= 9):
            raise ValueError("液氮罐的列数和层数需要在 1 到 9 之间。")
        freezer_id = self.current_freezer_id
        occupied_codes = {
            code for code, record in self.current_freezer.records.items() if record.occupied
        }
        occupied_codes.update(
            code
            for (stored_id, code, _position), sample in self.box_samples.items()
            if stored_id == freezer_id and sample.occupied
        )
        return sorted(
            (
                code
                for code in occupied_codes
                if (parsed := parse_unit_code(code))
                and (parsed[0] > columns or parsed[1] > layers)
            ),
            key=lambda code: tuple(int(char) for char in code),
        )

    def configure_storage(self, columns: int, layers: int) -> None:
        """Atomically resize the current tank without discarding inventory."""
        conflicts = self.storage_resize_conflicts(columns, layers)
        if conflicts:
            raise ValueError(
                f"新规格范围外仍有库存（盒位：{'、'.join(conflicts)}），请先移动或清空。"
            )
        if (columns, layers) == (self.storage_columns, self.storage_layers):
            return

        previous_freezers = copy.deepcopy(self.freezers)
        previous_layouts = copy.deepcopy(self.box_layouts)
        previous_samples = copy.deepcopy(self.box_samples)
        freezer_id = self.current_freezer_id

        def outside(code: str) -> bool:
            parsed = parse_unit_code(code)
            return bool(parsed and (parsed[0] > columns or parsed[1] > layers))

        self.current_freezer.storage_columns = columns
        self.current_freezer.storage_layers = layers
        for code in tuple(self.current_freezer.records):
            if outside(code):
                self.current_freezer.records.pop(code, None)
        for key in tuple(self.box_layouts):
            if key[0] == freezer_id and outside(key[1]):
                self.box_layouts.pop(key, None)
        for key in tuple(self.box_samples):
            if key[0] == freezer_id and outside(key[1]):
                self.box_samples.pop(key, None)
        try:
            self.save()
        except (OSError, ValueError):
            self.freezers = previous_freezers
            self.box_layouts = previous_layouts
            self.box_samples = previous_samples
            raise

    @property
    def records(self) -> dict[str, UnitRecord]:
        return self.current_freezer.records

    def list_freezers(self, include_archived: bool = False) -> list[tuple[str, str]]:
        return [
            (freezer_id, freezer.name)
            for freezer_id, freezer in self.freezers.items()
            if include_archived or not freezer.archived
        ]

    def list_archived_freezers(self) -> list[tuple[str, str, int]]:
        return [
            (freezer_id, freezer.name, sum(r.occupied for r in freezer.records.values()))
            for freezer_id, freezer in self.freezers.items()
            if freezer.archived
        ]

    def _validate_name(self, name: str, exclude_id: str | None = None) -> str:
        cleaned = name.strip()
        if not cleaned:
            raise ValueError("液氮罐名称不能为空。")
        if len(cleaned) > 50:
            raise ValueError("液氮罐名称不能超过 50 个字符。")
        if any(
            freezer_id != exclude_id and freezer.name.casefold() == cleaned.casefold()
            for freezer_id, freezer in self.freezers.items()
        ):
            raise ValueError("已经存在同名液氮罐，请使用其他名称。")
        return cleaned

    def add_freezer(self, name: str) -> str:
        freezer_id = f"freezer-{uuid.uuid4().hex[:10]}"
        self.freezers[freezer_id] = FreezerData(self._validate_name(name), {})
        self.current_freezer_id = freezer_id
        self.save()
        return freezer_id

    def rename_freezer(self, freezer_id: str, name: str) -> None:
        if freezer_id not in self.freezers:
            raise ValueError("找不到要重命名的液氮罐。")
        self.freezers[freezer_id].name = self._validate_name(name, freezer_id)
        self.save()

    def switch_freezer(self, freezer_id: str) -> None:
        if freezer_id not in self.freezers or self.freezers[freezer_id].archived:
            raise ValueError("找不到所选的未归档液氮罐。")
        self.current_freezer_id = freezer_id
        self.save()

    def archive_freezer(self, freezer_id: str) -> None:
        active = [key for key, value in self.freezers.items() if not value.archived]
        if freezer_id not in active:
            raise ValueError("找不到要归档的液氮罐。")
        if len(active) <= 1:
            raise ValueError("至少需要保留一个未归档液氮罐。")
        self.freezers[freezer_id].archived = True
        if self.current_freezer_id == freezer_id:
            self.current_freezer_id = next(key for key in active if key != freezer_id)
        self.save()

    def restore_freezer(self, freezer_id: str) -> None:
        if freezer_id not in self.freezers or not self.freezers[freezer_id].archived:
            raise ValueError("找不到要恢复的归档液氮罐。")
        self.freezers[freezer_id].archived = False
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._connect()) as connection, connection:
                self._create_schema(connection)
                connection.commit()
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("DELETE FROM box_samples")
                connection.execute("DELETE FROM box_layouts")
                connection.execute("DELETE FROM unit_records")
                connection.execute("DELETE FROM freezers")
                now = datetime.now().isoformat(timespec="seconds")
                for freezer_id, freezer in self.freezers.items():
                    connection.execute(
                        "INSERT INTO freezers(id,name,archived,storage_columns,storage_layers,created_at) VALUES(?,?,?,?,?,?)",
                        (
                            freezer_id,
                            freezer.name,
                            int(freezer.archived),
                            freezer.storage_columns,
                            freezer.storage_layers,
                            now,
                        ),
                    )
                    for code, record in sorted(freezer.records.items()):
                        if not record.occupied or not parse_unit_code(code):
                            continue
                        field_names = ",".join(RECORD_FIELDS)
                        placeholders = ",".join("?" for _ in range(2 + len(RECORD_FIELDS)))
                        connection.execute(
                            f"INSERT INTO unit_records(freezer_id,code,{field_names}) VALUES({placeholders})",
                            (freezer_id, code, *[getattr(record, field) for field in RECORD_FIELDS]),
                        )
                for (freezer_id, code), layout in sorted(self.box_layouts.items()):
                    if freezer_id not in self.freezers or not parse_unit_code(code):
                        continue
                    connection.execute(
                        "INSERT INTO box_layouts(freezer_id,unit_code,rows,columns_count,name) VALUES(?,?,?,?,?)",
                        (freezer_id, code, layout.rows, layout.columns, layout.name),
                    )
                for (freezer_id, code, position), sample in sorted(self.box_samples.items()):
                    if freezer_id not in self.freezers or not parse_unit_code(code) or not sample.occupied:
                        continue
                    row_index, column_index = self.parse_box_position(position)
                    field_names = ",".join(BOX_SAMPLE_FIELDS)
                    placeholders = ",".join("?" for _ in range(5 + len(BOX_SAMPLE_FIELDS)))
                    connection.execute(
                        f"INSERT INTO box_samples(freezer_id,unit_code,position,row_index,column_index,{field_names}) VALUES({placeholders})",
                        (freezer_id, code, position, row_index, column_index, *[getattr(sample, field) for field in BOX_SAMPLE_FIELDS]),
                    )
                connection.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES('current_freezer_id',?)",
                    (self.current_freezer_id,),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES('inventory_default_low',?)",
                    (str(self.default_inventory_alert[0]),),
                )
                connection.execute(
                    "INSERT OR REPLACE INTO settings(key,value) VALUES('inventory_default_warning',?)",
                    (str(self.default_inventory_alert[1]),),
                )
                connection.execute("DELETE FROM inventory_alerts")
                for sample_key, (sample_name, low_threshold, warning_threshold) in sorted(self.inventory_alerts.items()):
                    connection.execute(
                        "INSERT INTO inventory_alerts(sample_key,sample_name,low_threshold,warning_threshold) VALUES(?,?,?,?)",
                        (sample_key, sample_name, low_threshold, warning_threshold),
                    )
                event_fields = tuple(InventoryEvent.__dataclass_fields__)
                event_columns = ",".join(event_fields)
                event_placeholders = ",".join("?" for _ in event_fields)
                for event in self.inventory_events:
                    connection.execute(
                        f"INSERT OR IGNORE INTO inventory_events({event_columns}) VALUES({event_placeholders})",
                        tuple(getattr(event, field) for field in event_fields),
                    )
        except sqlite3.DatabaseError as exc:
            raise OSError(f"本地数据库写入失败：{exc}") from exc
        if len(self.inventory_events) > EVENT_MEMORY_CACHE_LIMIT:
            self.inventory_events = self.inventory_events[-EVENT_MEMORY_CACHE_LIMIT:]

    @staticmethod
    def _new_batch_id(prefix: str) -> str:
        return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6].upper()}"

    def _record_event(
        self,
        action: str,
        *,
        freezer_id: str,
        code: str,
        position: str,
        sample: BoxSample,
        operator: str = "",
        operation_date: str = "",
        batch_id: str = "",
        target_code: str = "",
        target_position: str = "",
        notes: str = "",
    ) -> InventoryEvent:
        freezer = self.freezers.get(freezer_id)
        now = datetime.now()
        event = InventoryEvent(
            event_id=f"event-{uuid.uuid4().hex}",
            batch_id=batch_id or self._new_batch_id(action[:3].upper()),
            action=action,
            freezer_id=freezer_id,
            freezer_name=freezer.name if freezer else "",
            unit_code=code,
            position=position.upper(),
            target_unit_code=target_code,
            target_position=target_position.upper(),
            sample_name=sample.sample_name,
            sample_type=sample.sample_type,
            operator=operator.strip() if action in {"IN", "OUT"} else "",
            operation_date=(operation_date.strip() or now.date().isoformat()),
            frozen_date=sample.stored_date.strip(),
            notes=(
                f"{notes.strip()}；{sample.notes.strip()}"
                if notes.strip() and sample.notes.strip() and notes.strip() != sample.notes.strip()
                else (notes.strip() or sample.notes.strip())
            ),
            created_at=now.isoformat(timespec="seconds"),
        )
        self.inventory_events.append(event)
        return event

    def list_inventory_events(
        self,
        query: str = "",
        *,
        action: str = "",
        actions: Sequence[str] | None = None,
        freezer_id: str = "",
        date_from: str = "",
        date_to: str = "",
        limit: int | None = None,
        offset: int = 0,
    ) -> list[InventoryEvent]:
        where_sql, parameters = self._inventory_event_filter_sql(
            query=query,
            action=action,
            actions=actions,
            freezer_id=freezer_id,
            date_from=date_from,
            date_to=date_to,
        )
        sql = f"SELECT * FROM inventory_events {where_sql} ORDER BY created_at DESC, rowid DESC"
        if limit is not None:
            clean_limit = max(0, int(limit))
            if clean_limit == 0:
                return []
            sql += " LIMIT ? OFFSET ?"
            parameters.extend((clean_limit, max(0, int(offset))))
        elif offset:
            sql += " LIMIT -1 OFFSET ?"
            parameters.append(max(0, int(offset)))
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(sql, parameters).fetchall()
        except sqlite3.DatabaseError as exc:
            raise OSError(f"出入库登记无法读取：{exc}") from exc
        return [
            InventoryEvent(**{field: str(row[field]) for field in InventoryEvent.__dataclass_fields__})
            for row in rows
        ]

    def count_inventory_events(
        self,
        query: str = "",
        *,
        action: str = "",
        actions: Sequence[str] | None = None,
        freezer_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> int:
        where_sql, parameters = self._inventory_event_filter_sql(
            query=query,
            action=action,
            actions=actions,
            freezer_id=freezer_id,
            date_from=date_from,
            date_to=date_to,
        )
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    f"SELECT COUNT(*) FROM inventory_events {where_sql}",
                    parameters,
                ).fetchone()
        except sqlite3.DatabaseError as exc:
            raise OSError(f"出入库登记数量无法读取：{exc}") from exc
        return int(row[0]) if row else 0

    def inventory_event_action_counts(self) -> dict[str, int]:
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    "SELECT action, COUNT(*) AS amount FROM inventory_events "
                    "WHERE action != 'IMPORT' GROUP BY action"
                ).fetchall()
        except sqlite3.DatabaseError as exc:
            raise OSError(f"出入库登记统计无法读取：{exc}") from exc
        return {str(row["action"]): int(row["amount"]) for row in rows}

    @staticmethod
    def _inventory_event_filter_sql(
        *,
        query: str = "",
        action: str = "",
        actions: Sequence[str] | None = None,
        freezer_id: str = "",
        date_from: str = "",
        date_to: str = "",
    ) -> tuple[str, list[object]]:
        conditions = ["action != 'IMPORT'"]
        parameters: list[object] = []
        if actions is not None:
            selected_actions = sorted(set(actions))
            if not selected_actions:
                conditions.append("0")
            else:
                placeholders = ",".join("?" for _ in selected_actions)
                conditions.append(f"action IN ({placeholders})")
                parameters.extend(selected_actions)
        if action:
            conditions.append("action = ?")
            parameters.append(action)
        if freezer_id:
            conditions.append("freezer_id = ?")
            parameters.append(freezer_id)
        start = date_from.replace("-", "").strip()
        end = date_to.replace("-", "").strip()
        if start:
            conditions.append("REPLACE(operation_date, '-', '') >= ?")
            parameters.append(start)
        if end:
            conditions.append("REPLACE(operation_date, '-', '') <= ?")
            parameters.append(end)
        needle = query.strip()
        if needle:
            searchable = (
                "sample_name || ' ' || sample_type || ' ' || operator || ' ' || "
                "freezer_name || ' ' || unit_code || ' ' || position || ' ' || "
                "target_unit_code || ' ' || target_position"
            )
            conditions.append(f"LOWER({searchable}) LIKE LOWER(?)")
            parameters.append(f"%{needle}%")
        return "WHERE " + " AND ".join(conditions), parameters

    def create_backup(self, label: str = "手动备份") -> Path:
        self.save()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        safe_label = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff_-]+", "_", label).strip("_") or "备份"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        target = self.backup_dir / f"{stamp}_{safe_label}.db"
        with closing(self._connect()) as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)
        return target

    def list_backups(self) -> list[tuple[Path, datetime, int]]:
        if not self.backup_dir.exists():
            return []
        return [
            (path, datetime.fromtimestamp(path.stat().st_mtime), path.stat().st_size)
            for path in sorted(self.backup_dir.glob("*.db"), reverse=True)
        ]

    def prune_backups(self, keep: int) -> int:
        if keep < 1 or keep > 200:
            raise ValueError("备份保留数量需要在 1 到 200 之间。")
        backups = [item[0] for item in self.list_backups()]
        removed = 0
        backup_root = self.backup_dir.resolve()
        for path in backups[keep:]:
            if path.resolve().parent != backup_root:
                raise ValueError("备份路径校验失败，已停止清理。")
            path.unlink()
            removed += 1
        return removed

    def delete_backup(self, backup_path: Path) -> None:
        """Delete exactly one database file from this repository's backup folder."""
        backup_path = Path(backup_path)
        backup_root = self.backup_dir.resolve()
        try:
            resolved = backup_path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError("所选备份不存在，可能已经被删除。") from exc
        if resolved.parent != backup_root or resolved.suffix.lower() != ".db":
            raise ValueError("只能删除本程序备份目录中的数据库文件。")
        if not resolved.is_file():
            raise ValueError("所选备份不是有效的数据库文件。")
        resolved.unlink()

    def restore_backup(self, backup_path: Path) -> Path:
        backup_path = Path(backup_path)
        if backup_path.parent.resolve() != self.backup_dir.resolve() or not backup_path.exists():
            raise ValueError("只能恢复本程序备份目录中的数据库文件。")
        try:
            with closing(self._connect(backup_path)) as connection:
                if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise ValueError("备份数据库完整性检查未通过。")
                connection.execute("SELECT 1 FROM freezers LIMIT 1").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"无效的备份数据库：{exc}") from exc
        safety_backup = self.create_backup("恢复前自动备份")
        shutil.copy2(backup_path, self.path)
        self.load()
        return safety_backup

    def import_freezers(
        self,
        imported: list[tuple[str, dict[str, dict[str, str]]]],
        *,
        overwrite: bool,
    ) -> dict[str, int]:
        previous = copy.deepcopy(self.freezers)
        previous_layouts = copy.deepcopy(self.box_layouts)
        previous_samples = copy.deepcopy(self.box_samples)
        previous_events = copy.deepcopy(self.inventory_events)
        previous_current = self.current_freezer_id
        self.last_import_backup = self.create_backup("Excel导入前")
        created = updated = record_count = 0
        try:
            for raw_name, incoming_records in imported:
                name = raw_name.strip()[:50] or f"导入液氮罐 {len(self.freezers) + 1}"
                matching_id = next(
                    (key for key, freezer in self.freezers.items() if freezer.name.casefold() == name.casefold()),
                    None,
                )
                created_freezer = matching_id is None
                if matching_id is None:
                    matching_id = f"freezer-{uuid.uuid4().hex[:10]}"
                    self.freezers[matching_id] = FreezerData(name, {})
                    created += 1
                else:
                    self.freezers[matching_id].archived = False
                    updated += 1
                target = self.freezers[matching_id]
                tube_records = {
                    key: value
                    for key, value in incoming_records.items()
                    if re.fullmatch(r"[1-9][1-9]/[A-Za-z]+[1-9][0-9]*", key)
                }
                if overwrite:
                    target.records = {}
                    for key in tuple(self.box_samples):
                        if key[0] == matching_id:
                            self.box_samples.pop(key, None)
                if tube_records:
                    converted_samples: dict[tuple[str, str, str], BoxSample] = {}
                    for full_position, value in tube_records.items():
                        code, position = full_position.split("/", 1)
                        sample = BoxSample.from_dict(value)
                        if sample.occupied:
                            if not sample.stored_by.strip():
                                raise ValueError(f"{name} 的 {full_position} 未填写入库人。")
                            converted_samples[(matching_id, code, position.upper())] = sample
                    self.box_samples.update(converted_samples)
                    imported_codes = {key[1] for key in converted_samples}
                    # Infer a safe layout for manually edited workbooks containing
                    # positions outside the default 9 x 9 box.
                    for code in imported_codes:
                        positions = [
                            key[2]
                            for key in converted_samples
                            if key[1] == code
                        ]
                        max_row = max((self.parse_box_position(pos)[0] for pos in positions), default=9)
                        max_column = max((self.parse_box_position(pos)[1] for pos in positions), default=9)
                        if max_row > 9 or max_column > 9:
                            self.box_layouts[(matching_id, code)] = BoxLayout(
                                rows=max(9, max_row),
                                columns=max(9, max_column),
                                name=f"细胞冻存盒 {code}",
                            )
                    converted_count = len(converted_samples)
                    storage_codes = imported_codes
                else:
                    converted = {
                        code: UnitRecord.from_dict(record)
                        for code, record in incoming_records.items()
                        if parse_unit_code(code) and UnitRecord.from_dict(record).occupied
                    }
                    target.records.update(converted)
                    converted_count = len(converted)
                    storage_codes = set(converted)
                coordinates = [parse_unit_code(code) for code in storage_codes]
                required_columns = max((item[0] for item in coordinates if item), default=1)
                required_layers = max((item[1] for item in coordinates if item), default=1)
                if not created_freezer and (
                    required_columns > target.storage_columns
                    or required_layers > target.storage_layers
                ):
                    raise ValueError(
                        f"{name} 的导入位置超出当前规格 "
                        f"{target.storage_columns}列×{target.storage_layers}层，请先调整液氮罐规格。"
                    )
                if created_freezer:
                    target.storage_columns = max(4, required_columns)
                    target.storage_layers = max(5, required_layers)
                record_count += converted_count
            self.save()
        except (OSError, ValueError):
            self.freezers = previous
            self.box_layouts = previous_layouts
            self.box_samples = previous_samples
            self.inventory_events = previous_events
            self.current_freezer_id = previous_current
            self.save()
            raise
        return {"freezers": len(imported), "created": created, "updated": updated, "records": record_count}

    def get(self, code: str) -> UnitRecord:
        return self.records.get(code, UnitRecord())

    def set(self, code: str, record: UnitRecord) -> None:
        if not self.is_storage_code(code):
            raise ValueError(f"无效的冻存盒编号：{code}")
        if record.occupied:
            self.records[code] = record
        else:
            self.records.pop(code, None)
        self.save()

    def clear(self, code: str) -> None:
        self.records.pop(code, None)
        self.save()

    @staticmethod
    def box_position(row: int, column: int) -> str:
        if row < 1 or column < 1:
            raise ValueError("盒内行列编号必须从 1 开始。")
        letters = ""
        value = row
        while value:
            value, remainder = divmod(value - 1, 26)
            letters = chr(65 + remainder) + letters
        return f"{letters}{column}"

    @staticmethod
    def parse_box_position(position: str) -> tuple[int, int]:
        match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", position.upper())
        if not match:
            raise ValueError(f"无效的盒内位置：{position}")
        row = 0
        for char in match.group(1):
            row = row * 26 + ord(char) - 64
        return row, int(match.group(2))

    def get_box_layout(self, code: str, freezer_id: str | None = None) -> BoxLayout:
        if not self.is_storage_code(code, freezer_id):
            raise ValueError(f"无效的盒位编号：{code}")
        layout = copy.deepcopy(self.box_layouts.get((freezer_id or self.current_freezer_id, code), BoxLayout()))
        layout.name = f"细胞冻存盒 {code}"
        return layout

    def configure_box(self, code: str, rows: int, columns: int, name: str = "") -> None:
        if not self.is_storage_code(code):
            raise ValueError(f"无效的盒位编号：{code}")
        if not (1 <= rows <= 20 and 1 <= columns <= 20):
            raise ValueError("盒子布局的行数和列数需要在 1 到 20 之间。")
        outside = []
        for (freezer_id, unit_code, position), sample in self.box_samples.items():
            if freezer_id != self.current_freezer_id or unit_code != code or not sample.occupied:
                continue
            row, column = self.parse_box_position(position)
            if row > rows or column > columns:
                outside.append(position)
        if outside:
            raise ValueError(f"新布局范围外仍有 {len(outside)} 个样品（如 {outside[0]}），请先移动或清空。")
        self.box_layouts[(self.current_freezer_id, code)] = BoxLayout(rows, columns, f"细胞冻存盒 {code}")
        self.save()

    def get_box_samples(self, code: str, freezer_id: str | None = None) -> dict[str, BoxSample]:
        target_id = freezer_id or self.current_freezer_id
        return {
            position: copy.deepcopy(sample)
            for (stored_id, unit_code, position), sample in self.box_samples.items()
            if stored_id == target_id and unit_code == code and sample.occupied
        }

    def find_empty_positions(
        self,
        required: int,
        *,
        all_freezers: bool = False,
        continuous: bool = True,
    ) -> list[dict[str, object]]:
        """Return ranked cryoboxes with recommended empty positions."""
        if required < 1:
            raise ValueError("所需冻存管数量至少为 1。")
        target_ids = [
            freezer_id
            for freezer_id, freezer in self.freezers.items()
            if not freezer.archived and (all_freezers or freezer_id == self.current_freezer_id)
        ]
        results: list[dict[str, object]] = []
        for freezer_id in target_ids:
            freezer = self.freezers[freezer_id]
            for code in all_unit_codes(freezer.storage_columns, freezer.storage_layers):
                layout = self.get_box_layout(code, freezer_id)
                occupied = set(self.get_box_samples(code, freezer_id))
                empty = [
                    self.box_position(row, column)
                    for row in range(1, layout.rows + 1)
                    for column in range(1, layout.columns + 1)
                    if self.box_position(row, column) not in occupied
                ]
                if not empty:
                    continue
                recommendation = self._recommended_empty_positions(
                    empty, layout.rows, layout.columns, required, continuous
                )
                contiguous_count = self._longest_empty_run(empty, layout.rows, layout.columns)
                results.append(
                    {
                        "freezer_id": freezer_id,
                        "freezer_name": freezer.name,
                        "code": code,
                        "rows": layout.rows,
                        "columns": layout.columns,
                        "empty_count": len(empty),
                        "occupied_count": len(occupied),
                        "fits": len(empty) >= required,
                        "contiguous_count": contiguous_count,
                        "recommended_positions": recommendation,
                    }
                )
        return sorted(
            results,
            key=lambda item: (
                not bool(item["fits"]),
                -min(int(item["contiguous_count"]), required) if continuous else 0,
                int(item["occupied_count"]) == 0,
                int(item["empty_count"]),
                str(item["freezer_name"]).casefold(),
                str(item["code"]),
            ),
        )

    def _recommended_empty_positions(
        self, empty: list[str], rows: int, columns: int, required: int, continuous: bool
    ) -> list[str]:
        empty_set = set(empty)
        if continuous:
            for row in range(1, rows + 1):
                run: list[str] = []
                for column in range(1, columns + 1):
                    position = self.box_position(row, column)
                    if position in empty_set:
                        run.append(position)
                        if len(run) >= required:
                            return run[:required]
                    else:
                        run.clear()
        return empty[:required]

    def _longest_empty_run(self, empty: list[str], rows: int, columns: int) -> int:
        empty_set = set(empty)
        longest = 0
        for row in range(1, rows + 1):
            current = 0
            for column in range(1, columns + 1):
                if self.box_position(row, column) in empty_set:
                    current += 1
                    longest = max(longest, current)
                else:
                    current = 0
        return longest

    def get_box_sample(self, code: str, position: str) -> BoxSample:
        return copy.deepcopy(self.box_samples.get((self.current_freezer_id, code, position.upper()), BoxSample()))

    def set_box_sample(self, code: str, position: str, sample: BoxSample) -> None:
        layout = self.get_box_layout(code)
        row, column = self.parse_box_position(position)
        if row > layout.rows or column > layout.columns:
            raise ValueError(f"盒内位置 {position} 超出当前 {layout.rows}×{layout.columns} 布局。")
        key = (self.current_freezer_id, code, position.upper())
        previous = copy.deepcopy(self.box_samples.get(key, BoxSample()))
        previous_event_count = len(self.inventory_events)
        if sample.occupied:
            if not sample.stored_by.strip():
                raise ValueError("入库人不能为空。")
            if not sample.stored_date.strip():
                raise ValueError("入库日期不能为空。")
            normalized = copy.deepcopy(sample)
            normalized.quantity = "1"
            normalized.sample_id = ""
            self.box_samples[key] = normalized
            self._record_event(
                "ADJUST" if previous.occupied else "IN",
                freezer_id=self.current_freezer_id,
                code=code,
                position=position,
                sample=normalized,
                operator=normalized.stored_by,
                operation_date=normalized.stored_date,
            )
        else:
            self.box_samples.pop(key, None)
            if previous.occupied:
                self._record_event(
                    "CLEAR",
                    freezer_id=self.current_freezer_id,
                    code=code,
                    position=position,
                    sample=previous,
                )
        try:
            self.save()
        except (OSError, ValueError):
            if previous.occupied:
                self.box_samples[key] = previous
            else:
                self.box_samples.pop(key, None)
            del self.inventory_events[previous_event_count:]
            raise

    def move_box_sample(self, code: str, source: str, target: str, *, undo: bool = False) -> BoxSample:
        """Move one cryotube inside a box and persist it as one atomic operation."""
        layout = self.get_box_layout(code)
        source = source.upper()
        target = target.upper()
        if source == target:
            raise ValueError("起始孔位和目标孔位不能相同。")
        for position in (source, target):
            row, column = self.parse_box_position(position)
            if row > layout.rows or column > layout.columns:
                raise ValueError(f"盒内位置 {position} 超出当前 {layout.rows}×{layout.columns} 布局。")

        source_key = (self.current_freezer_id, code, source)
        target_key = (self.current_freezer_id, code, target)
        sample = self.box_samples.get(source_key)
        if sample is None or not sample.occupied:
            raise ValueError(f"起始孔位 {source} 中没有可移动的细胞。")
        if self.box_samples.get(target_key, BoxSample()).occupied:
            raise ValueError(f"目标孔位 {target} 已有细胞，请选择空孔位。")

        moved_sample = copy.deepcopy(sample)
        self.box_samples[target_key] = moved_sample
        self.box_samples.pop(source_key, None)
        previous_event_count = len(self.inventory_events)
        self._record_event(
            "IN_MOVE_UNDO" if undo else "IN_MOVE",
            freezer_id=self.current_freezer_id,
            code=code,
            position=source,
            target_code=code,
            target_position=target,
            sample=moved_sample,
            operator=moved_sample.stored_by,
            operation_date=date.today().isoformat(),
            batch_id=self._new_batch_id("MOV"),
            notes="撤销快捷移动" if undo else "快捷移动",
        )
        try:
            self.save()
        except (OSError, ValueError):
            self.box_samples[source_key] = sample
            self.box_samples.pop(target_key, None)
            del self.inventory_events[previous_event_count:]
            raise
        return copy.deepcopy(moved_sample)

    def batch_set_box_samples(self, code: str, positions: list[str], sample: BoxSample) -> int:
        if not sample.occupied:
            raise ValueError("细胞名称不能为空。")
        if not sample.stored_by.strip():
            raise ValueError("入库人不能为空。")
        if not sample.stored_date.strip():
            raise ValueError("入库日期不能为空。")
        layout = self.get_box_layout(code)
        valid_positions = []
        for position in sorted({position.upper() for position in positions}):
            row, column = self.parse_box_position(position)
            if row > layout.rows or column > layout.columns:
                raise ValueError(f"盒内位置 {position} 超出当前布局。")
            valid_positions.append(position)

        conflicts = [
            position
            for position in valid_positions
            if self.box_samples.get(
                (self.current_freezer_id, code, position),
                BoxSample(),
            ).occupied
        ]
        if conflicts:
            preview = "、".join(conflicts[:16])
            if len(conflicts) > 16:
                preview += f" 等{len(conflicts)}个孔位"
            raise ValueError(
                f"以下孔位已有细胞，不能批量入库：{preview}。"
                "请先出库或清除这些孔位后再入库。"
            )

        keys = [
            (self.current_freezer_id, code, position)
            for position in valid_positions
        ]
        previous = {
            key: copy.deepcopy(self.box_samples[key])
            for key in keys
            if key in self.box_samples
        }
        previous_event_count = len(self.inventory_events)
        batch_id = self._new_batch_id("IN")
        try:
            for position, key in zip(valid_positions, keys):
                item = copy.deepcopy(sample)
                item.sample_name = item.sample_name.replace("{position}", position)
                item.quantity = "1"
                item.sample_id = ""
                self.box_samples[key] = item
                self._record_event(
                    "IN",
                    freezer_id=self.current_freezer_id,
                    code=code,
                    position=position,
                    sample=item,
                    operator=item.stored_by,
                    operation_date=item.stored_date,
                    batch_id=batch_id,
                )
            self.save()
        except (OSError, ValueError):
            for key in keys:
                if key in previous:
                    self.box_samples[key] = previous[key]
                else:
                    self.box_samples.pop(key, None)
            del self.inventory_events[previous_event_count:]
            raise
        return len(valid_positions)

    def batch_checkout_box_samples(
        self,
        code: str,
        positions: list[str],
        operator: str,
        operation_date: str,
    ) -> int:
        clean_operator = operator.strip()
        clean_date = operation_date.strip()
        if not clean_operator:
            raise ValueError("出库人不能为空。")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", clean_date):
            raise ValueError("出库日期请填写为 YYYY-MM-DD 或 YYYYMMDD。")
        try:
            parsed_date = datetime.strptime(clean_date, "%Y%m%d").date() if len(clean_date) == 8 else date.fromisoformat(clean_date)
        except ValueError as exc:
            raise ValueError("出库日期不是有效日期。") from exc
        clean_date = parsed_date.isoformat()
        normalized_positions = sorted({position.upper() for position in positions}, key=self.parse_box_position)
        if not normalized_positions:
            raise ValueError("请至少选择一个需要出库的孔位。")
        missing = [
            position
            for position in normalized_positions
            if not self.box_samples.get((self.current_freezer_id, code, position), BoxSample()).occupied
        ]
        if missing:
            raise ValueError(f"以下孔位没有细胞，不能出库：{'、'.join(missing[:16])}。")
        keys = [(self.current_freezer_id, code, position) for position in normalized_positions]
        previous = {key: copy.deepcopy(self.box_samples[key]) for key in keys}
        previous_event_count = len(self.inventory_events)
        batch_id = self._new_batch_id("OUT")
        try:
            for key in keys:
                sample = self.box_samples.pop(key)
                self._record_event(
                    "OUT",
                    freezer_id=self.current_freezer_id,
                    code=code,
                    position=key[2],
                    sample=sample,
                    operator=clean_operator,
                    operation_date=clean_date,
                    batch_id=batch_id,
                )
            self.save()
        except (OSError, ValueError):
            self.box_samples.update(previous)
            del self.inventory_events[previous_event_count:]
            raise
        return len(keys)

    def batch_clear_box_samples(self, code: str, positions: list[str]) -> int:
        keys = [
            (self.current_freezer_id, code, position.upper())
            for position in sorted(set(positions), key=self.parse_box_position)
            if self.box_samples.get((self.current_freezer_id, code, position.upper()), BoxSample()).occupied
        ]
        previous = {key: copy.deepcopy(self.box_samples[key]) for key in keys}
        previous_event_count = len(self.inventory_events)
        batch_id = self._new_batch_id("CLR")
        try:
            for key in keys:
                sample = self.box_samples.pop(key)
                self._record_event(
                    "CLEAR",
                    freezer_id=self.current_freezer_id,
                    code=code,
                    position=key[2],
                    sample=sample,
                    batch_id=batch_id,
                )
            self.save()
        except (OSError, ValueError):
            self.box_samples.update(previous)
            del self.inventory_events[previous_event_count:]
            raise
        return len(keys)

    def clear_boxes(self, codes: list[str]) -> tuple[int, int]:
        """Clear all cryotubes from selected boxes and return (boxes, tubes)."""
        selected = {
            code for code in codes
            if self.is_storage_code(code) and self.box_has_samples(code)
        }
        if not selected:
            return 0, 0
        keys = [
            key for key, sample in self.box_samples.items()
            if key[0] == self.current_freezer_id
            and key[1] in selected
            and sample.occupied
        ]
        previous_samples = {key: self.box_samples[key] for key in keys}
        previous_records = {
            code: self.current_freezer.records[code]
            for code in selected
            if code in self.current_freezer.records
        }
        previous_event_count = len(self.inventory_events)
        batch_id = self._new_batch_id("CLR")
        for key in keys:
            self._record_event(
                "CLEAR",
                freezer_id=self.current_freezer_id,
                code=key[1],
                position=key[2],
                sample=previous_samples[key],
                batch_id=batch_id,
            )
        for key in keys:
            self.box_samples.pop(key, None)
        # Remove any legacy -80°C box-level record kept at the same code too.
        for code in selected:
            self.current_freezer.records.pop(code, None)
        try:
            self.save()
        except (OSError, ValueError):
            self.box_samples.update(previous_samples)
            self.current_freezer.records.update(previous_records)
            del self.inventory_events[previous_event_count:]
            raise
        return len(selected), len(keys)

    def inventory_summary(self, query: str = "") -> list[dict[str, object]]:
        needle = query.strip().casefold()
        grouped: dict[str, dict[str, object]] = {}
        for (freezer_id, code, position), sample in self.box_samples.items():
            if not sample.occupied or freezer_id not in self.freezers or self.freezers[freezer_id].archived:
                continue
            searchable = " ".join((sample.sample_name, sample.sample_type, sample.stored_by, code, position)).casefold()
            if needle and needle not in searchable:
                continue
            key = sample.sample_name.strip().casefold()
            item = grouped.setdefault(key, {"sample_name": sample.sample_name.strip(), "positions": [], "types": set()})
            item["positions"].append((freezer_id, self.freezers[freezer_id].name, code, position))
            if sample.sample_type.strip():
                item["types"].add(sample.sample_type.strip())
        return sorted(
            grouped.values(),
            key=lambda item: inventory_name_sort_key(str(item["sample_name"])),
        )

    @staticmethod
    def inventory_alert_key(sample_name: str) -> str:
        return sample_name.strip().casefold()

    def get_inventory_alert(self, sample_name: str) -> tuple[int, int, bool]:
        configured = self.inventory_alerts.get(self.inventory_alert_key(sample_name))
        if configured is None:
            return self.default_inventory_alert[0], self.default_inventory_alert[1], False
        return configured[1], configured[2], True

    def set_default_inventory_alert(self, low_threshold: int, warning_threshold: int) -> None:
        if low_threshold < 0 or warning_threshold < 0:
            raise ValueError("预警值不能小于 0。")
        if warning_threshold < low_threshold:
            raise ValueError("提醒库存不能小于最低库存。")
        self.default_inventory_alert = (low_threshold, warning_threshold)
        self.save()

    def set_inventory_alert(self, sample_name: str, low_threshold: int, warning_threshold: int) -> None:
        clean_name = sample_name.strip()
        if not clean_name:
            raise ValueError("细胞名称不能为空。")
        if low_threshold < 0 or warning_threshold < 0:
            raise ValueError("预警值不能小于 0。")
        if warning_threshold < low_threshold:
            raise ValueError("提醒库存不能小于最低库存。")
        self.inventory_alerts[self.inventory_alert_key(clean_name)] = (
            clean_name,
            low_threshold,
            warning_threshold,
        )
        self.save()

    def inventory_locations(self, sample_name: str) -> list[dict[str, object]]:
        """Return exact-name inventory grouped by freezer and physical box."""
        target = sample_name.strip().casefold()
        grouped: dict[tuple[str, str], dict[str, object]] = {}
        for (freezer_id, code, position), sample in self.box_samples.items():
            freezer = self.freezers.get(freezer_id)
            if not sample.occupied or freezer is None or freezer.archived:
                continue
            if sample.sample_name.strip().casefold() != target:
                continue
            item = grouped.setdefault(
                (freezer_id, code),
                {
                    "freezer_id": freezer_id,
                    "freezer_name": freezer.name,
                    "code": code,
                    "box_name": f"细胞冻存盒 {code}",
                    "positions": [],
                    "samples": [],
                },
            )
            item["positions"].append(position)
            item["samples"].append((position, copy.deepcopy(sample)))
        locations = list(grouped.values())
        for item in locations:
            item["positions"] = sorted(item["positions"], key=self.parse_box_position)
            item["samples"] = sorted(item["samples"], key=lambda pair: self.parse_box_position(pair[0]))
        return sorted(locations, key=lambda item: (str(item["freezer_name"]).casefold(), str(item["code"])))

    def search_box_samples(
        self,
        query: str = "",
        *,
        sample_type: str = "",
        stored_by: str = "",
        date_from: str = "",
        date_to: str = "",
        all_freezers: bool = False,
    ) -> list[tuple[str, str, str, str, BoxSample]]:
        """Search physical box contents in one or all active freezers."""
        needle = query.strip().casefold()
        type_needle = sample_type.strip().casefold()
        person_needle = stored_by.strip().casefold()
        start = date_from.replace("-", "").strip()
        end = date_to.replace("-", "").strip()
        results: list[tuple[str, str, str, str, BoxSample]] = []
        for (stored_freezer_id, code, position), sample in self.box_samples.items():
            freezer = self.freezers.get(stored_freezer_id)
            if freezer is None or freezer.archived or not sample.occupied:
                continue
            if not all_freezers and stored_freezer_id != self.current_freezer_id:
                continue
            fields = (sample.sample_name, sample.sample_type, sample.stored_by, sample.notes, code, position, f"{code}/{position}")
            if needle and not any(needle in field.casefold() for field in fields):
                continue
            if type_needle and type_needle not in sample.sample_type.casefold():
                continue
            if person_needle and person_needle not in sample.stored_by.casefold():
                continue
            stored_date = sample.stored_date.replace("-", "")
            if start and (not stored_date or stored_date < start):
                continue
            if end and (not stored_date or stored_date > end):
                continue
            results.append((stored_freezer_id, freezer.name, code, position, copy.deepcopy(sample)))
        return sorted(
            results,
            key=lambda item: (item[1].casefold(), item[2], self.parse_box_position(item[3])),
        )

    def search_all_freezer_records(self, query: str) -> list[tuple[str, str, str, UnitRecord]]:
        """Search legacy box-level records across every active liquid-nitrogen tank."""
        needle = query.strip().casefold()
        results: list[tuple[str, str, str, UnitRecord]] = []
        for freezer_id, freezer in self.freezers.items():
            if freezer.archived:
                continue
            for code, record in freezer.records.items():
                if not record.occupied:
                    continue
                fields = (
                    code,
                    record.sample_name,
                    record.experiment_id,
                    record.sample_type,
                    record.stored_by,
                    record.claimed_by,
                    record.notes,
                )
                if needle and not any(needle in field.casefold() for field in fields):
                    continue
                results.append((freezer_id, freezer.name, code, copy.deepcopy(record)))
        return sorted(results, key=lambda item: (item[1].casefold(), item[2]))

    def advanced_box_search(
        self,
        query: str = "",
        *,
        sample_type: str = "",
        stored_by: str = "",
        date_from: str = "",
        date_to: str = "",
        occupied: bool = True,
    ) -> list[dict[str, object]]:
        """Return one selectable result per cryobox for the advanced-search UI."""
        if occupied:
            matches = self.search_box_samples(
                query,
                sample_type=sample_type,
                stored_by=stored_by,
                date_from=date_from,
                date_to=date_to,
            )
            grouped: dict[str, dict[str, object]] = {}
            for _freezer_id, _freezer_name, code, position, sample in matches:
                item = grouped.setdefault(code, {"code": code, "positions": [], "samples": []})
                item["positions"].append(position)
                item["samples"].append(copy.deepcopy(sample))
            return [grouped[code] for code in sorted(grouped)]

        # An empty-box query applies to box codes themselves. Cell-specific
        # filters cannot match a box which contains no cells.
        if any(value.strip() for value in (sample_type, stored_by, date_from, date_to)):
            return []
        needle = query.strip().casefold()
        results: list[dict[str, object]] = []
        for code in all_unit_codes(self.storage_columns, self.storage_layers):
            if self.box_has_samples(code):
                continue
            if needle and needle not in code.casefold() and needle not in f"细胞冻存盒 {code}".casefold():
                continue
            results.append({"code": code, "positions": [], "samples": []})
        return results

    @property
    def box_sample_count(self) -> int:
        return sum(
            sample.occupied
            for (freezer_id, _code, _position), sample in self.box_samples.items()
            if freezer_id == self.current_freezer_id
        )

    def batch_clear(self, codes: list[str]) -> int:
        removed = 0
        for code in set(codes):
            if self.records.pop(code, None) is not None:
                removed += 1
        self.save()
        return removed

    def box_has_samples(self, code: str, freezer_id: str | None = None) -> bool:
        return self.box_inventory_count(code, freezer_id) > 0

    def box_position_is_empty(self, freezer_id: str, code: str) -> bool:
        freezer = self.freezers.get(freezer_id)
        if freezer is None or freezer.archived:
            return False
        parsed = parse_unit_code(code)
        if (
            not parsed
            or parsed[0] > freezer.storage_columns
            or parsed[1] > freezer.storage_layers
        ):
            return False
        return (
            not freezer.records.get(code, UnitRecord()).occupied
            and not self.box_has_samples(code, freezer_id)
        )

    def move_boxes(self, source_codes: list[str], target_freezer_id: str, target_codes: list[str]) -> list[tuple[str, str]]:
        sources = sorted(set(source_codes))
        targets = list(dict.fromkeys(target_codes))
        if not sources or len(sources) != len(targets):
            raise ValueError("源冻存盒与目标盒位数量必须一致。")
        if target_freezer_id not in self.freezers or self.freezers[target_freezer_id].archived:
            raise ValueError("目标液氮罐不存在或已归档。")
        if any(not self.box_has_samples(code) for code in sources):
            raise ValueError("所选源盒位中包含没有细胞的冻存盒。")
        if any(not self.box_position_is_empty(target_freezer_id, code) for code in targets):
            raise ValueError("所选目标盒位中包含已占用或无效的位置。")
        if self.current_freezer_id == target_freezer_id and set(sources) & set(targets):
            raise ValueError("目标盒位不能与源盒位相同。")

        previous_freezers = copy.deepcopy(self.freezers)
        previous_layouts = copy.deepcopy(self.box_layouts)
        previous_samples = copy.deepcopy(self.box_samples)
        source_freezer_id = self.current_freezer_id
        try:
            mappings: list[tuple[str, str]] = []
            for source_code, target_code in zip(sources, targets):
                source_record = self.freezers[source_freezer_id].records.pop(source_code, None)
                if source_record and source_record.occupied:
                    self.freezers[target_freezer_id].records[target_code] = source_record
                source_layout = self.box_layouts.pop((source_freezer_id, source_code), None)
                if source_layout is not None:
                    source_layout.name = f"细胞冻存盒 {target_code}"
                    self.box_layouts[(target_freezer_id, target_code)] = source_layout
                sample_keys = [
                    key for key in self.box_samples
                    if key[0] == source_freezer_id and key[1] == source_code
                ]
                for key in sample_keys:
                    self.box_samples[(target_freezer_id, target_code, key[2])] = self.box_samples.pop(key)
                mappings.append((source_code, target_code))
            self.save()
            return mappings
        except (OSError, ValueError):
            self.freezers = previous_freezers
            self.box_layouts = previous_layouts
            self.box_samples = previous_samples
            raise

    def batch_move(self, codes: list[str], target_freezer_id: str, start_code: str) -> list[tuple[str, str]]:
        ordered = sorted({code for code in codes if code in self.records and self.records[code].occupied})
        if not ordered:
            raise ValueError("请至少选择一条已占用记录。")
        if target_freezer_id not in self.freezers or self.freezers[target_freezer_id].archived:
            raise ValueError("目标液氮罐不存在或已归档。")
        target_freezer = self.freezers[target_freezer_id]
        all_codes = all_unit_codes(target_freezer.storage_columns, target_freezer.storage_layers)
        if start_code not in all_codes:
            raise ValueError("目标起始冻存盒位编码无效。")
        previous = copy.deepcopy(self.freezers)
        previous_layouts = copy.deepcopy(self.box_layouts)
        previous_samples = copy.deepcopy(self.box_samples)
        source_freezer_id = self.current_freezer_id
        source = self.current_freezer
        target = self.freezers[target_freezer_id]
        records_to_move = [(code, source.records.pop(code)) for code in ordered]
        available = [
            code for code in all_codes[all_codes.index(start_code):]
            if not target.records.get(code, UnitRecord()).occupied
            and (target_freezer_id, code) not in self.box_layouts
            and not any(key[0] == target_freezer_id and key[1] == code for key in self.box_samples)
        ]
        if len(available) < len(records_to_move):
            self.freezers = previous
            raise ValueError("从目标起始冻存盒位向后没有足够的可用空位。")
        mappings: list[tuple[str, str]] = []
        for (source_code, record), target_code in zip(records_to_move, available):
            target.records[target_code] = record
            source_layout_key = (source_freezer_id, source_code)
            if source_layout_key in self.box_layouts:
                self.box_layouts[(target_freezer_id, target_code)] = self.box_layouts.pop(source_layout_key)
            sample_keys = [
                key for key in self.box_samples
                if key[0] == source_freezer_id and key[1] == source_code
            ]
            for key in sample_keys:
                sample = self.box_samples.pop(key)
                self.box_samples[(target_freezer_id, target_code, key[2])] = sample
            mappings.append((source_code, target_code))
        try:
            self.save()
        except OSError:
            self.freezers = previous
            self.box_layouts = previous_layouts
            self.box_samples = previous_samples
            raise
        return mappings

    def compartment_usage(self, column: int, layer: int) -> int:
        code = unit_code(column, layer)
        return int(self.box_has_samples(code))

    def layer_usage(self, layer: int) -> int:
        return sum(self.compartment_usage(column, layer) for column in range(1, self.storage_columns + 1))

    @property
    def used_count(self) -> int:
        return sum(
            self.compartment_usage(column, layer)
            for column in range(1, self.storage_columns + 1)
            for layer in range(1, self.storage_layers + 1)
        )

    def filter_records(
        self,
        *,
        query: str = "",
        sample_type: str = "",
        stored_by: str = "",
        date_from: str = "",
        date_to: str = "",
        status: str = "occupied",
    ) -> list[tuple[str, UnitRecord]]:
        needle = query.strip().casefold()
        sample_needle = sample_type.strip().casefold()
        person_needle = stored_by.strip().casefold()
        start = date_from.replace("-", "").strip()
        end = date_to.replace("-", "").strip()
        codes = (
            all_unit_codes(self.storage_columns, self.storage_layers)
            if status == "empty"
            else sorted(self.records)
        )
        results: list[tuple[str, UnitRecord]] = []
        for code in codes:
            record = self.records.get(code, UnitRecord())
            if status == "occupied" and not record.occupied:
                continue
            if status == "empty" and record.occupied:
                continue
            fields = (code, record.sample_name, record.experiment_id, record.sample_type, record.stored_by, record.claimed_by, record.notes)
            if needle and not any(needle in field.casefold() for field in fields):
                continue
            if sample_needle and sample_needle not in record.sample_type.casefold():
                continue
            if person_needle and person_needle not in record.stored_by.casefold():
                continue
            record_date = record.sample_date.replace("-", "")
            if start and (not record_date or record_date < start):
                continue
            if end and (not record_date or record_date > end):
                continue
            results.append((code, record))
        return results

    def search(self, query: str) -> list[tuple[str, UnitRecord]]:
        return self.filter_records(query=query, status="occupied")
