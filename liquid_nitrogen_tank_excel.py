from __future__ import annotations

import re
import posixpath
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, Protocol, Sequence
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape


class RecordLike(Protocol):
    sample_name: str
    stored_date: str
    experiment_id: str
    sample_type: str
    sample_count: str
    sample_date: str
    stored_by: str
    claimed_by: str
    claimed_date: str
    claimed_amount: str
    notes: str

    @property
    def occupied(self) -> bool: ...


class BoxSampleLike(Protocol):
    sample_name: str
    quantity: str
    sample_id: str
    sample_type: str
    stored_date: str
    stored_by: str
    notes: str

    @property
    def occupied(self) -> bool: ...


class InventoryEventLike(Protocol):
    batch_id: str
    action: str
    freezer_name: str
    unit_code: str
    position: str
    target_unit_code: str
    target_position: str
    sample_name: str
    sample_type: str
    operator: str
    operation_date: str
    frozen_date: str
    created_at: str
    notes: str


HEADERS = (
    "盒位编号",
    "列",
    "层",
    "状态",
    "样品名称",
    "入库日期",
    "实验编号",
    "样品类别",
    "样品个数",
    "采样日期",
    "入库人",
    "领用人",
    "领用日期",
    "领用量",
    "备注",
)

TUBE_HEADERS = (
    "冻存盒位",
    "列",
    "层",
    "盒内孔位",
    "完整位置",
    "细胞名称",
    "细胞类别",
    "入库日期",
    "入库人",
    "备注",
)

EVENT_HEADERS = (
    "序号", "操作日期", "细胞名称", "冻存日期", "位置",
    "出入库", "数量（管）", "操作人", "签名", "备注",
)

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"x": MAIN_NS, "r": DOC_REL_NS, "pr": PKG_REL_NS}


@dataclass(frozen=True)
class ImportIssue:
    severity: str
    sheet: str
    row: int
    field: str
    value: str
    message: str


@dataclass
class ImportPreview:
    # Legacy workbooks use two-character box keys (for example ``41``).
    # Current cryotube workbooks use full tube keys (for example ``41/A2``).
    freezers: list[tuple[str, dict[str, dict[str, str]]]]
    issues: list[ImportIssue]

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "错误" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "提醒" for issue in self.issues)

    @property
    def record_count(self) -> int:
        return sum(len(records) for _name, records in self.freezers)

    @property
    def can_import(self) -> bool:
        return bool(self.freezers) and self.error_count == 0


def _column_name(index: int) -> str:
    result = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def _excel_date(value: str) -> float | None:
    text = value.strip()
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            parsed = datetime.strptime(text, fmt).date()
            return float((parsed - date(1899, 12, 30)).days)
        except ValueError:
            continue
    return None


def _text_cell(reference: str, value: object, style: int = 3) -> str:
    text = escape(str(value))
    space = ' xml:space="preserve"' if text != text.strip() else ""
    return f'<c r="{reference}" s="{style}" t="inlineStr"><is><t{space}>{text}</t></is></c>'


def _number_cell(reference: str, value: int | float, style: int = 4) -> str:
    return f'<c r="{reference}" s="{style}"><v>{value}</v></c>'


def _formula_cell(reference: str, formula: str, cached: int | float, style: int) -> str:
    return f'<c r="{reference}" s="{style}"><f>{escape(formula)}</f><v>{cached}</v></c>'


def _date_cell(reference: str, value: str) -> str:
    serial = _excel_date(value)
    if serial is None:
        return _text_cell(reference, value)
    return _number_cell(reference, serial, 5)


def _record_value(record: RecordLike | None, name: str) -> str:
    return str(getattr(record, name, "")) if record is not None else ""


def _ledger_sheet(
    records: Mapping[str, RecordLike],
    storage_columns: int | None = None,
    storage_layers: int | None = None,
) -> str:
    rows = []
    header_cells = "".join(
        _text_cell(f"{_column_name(column)}1", header, 2)
        for column, header in enumerate(HEADERS, 1)
    )
    rows.append(f'<row r="1" ht="28" customHeight="1">{header_cells}</row>')

    row_number = 2
    coordinates = [
        (int(str(code)[0]), int(str(code)[1]))
        for code in records
        if re.fullmatch(r"[1-9][1-9]", str(code))
    ]
    storage_columns = storage_columns or max(4, max((item[0] for item in coordinates), default=4))
    storage_layers = storage_layers or max(5, max((item[1] for item in coordinates), default=5))
    for column in range(1, storage_columns + 1):
        for layer in range(1, storage_layers + 1):
            code = f"{column}{layer}"
            record = records.get(code)
            occupied = bool(record and record.occupied)
            status_style = 6 if occupied else 7
            sample_name = _record_value(record, "sample_name") or _record_value(record, "experiment_id")
            stored_date = _record_value(record, "stored_date") or _record_value(record, "sample_date")
            cells = [
                _text_cell(f"A{row_number}", code),
                _number_cell(f"B{row_number}", column),
                _number_cell(f"C{row_number}", layer),
                _text_cell(f"D{row_number}", "已占用" if occupied else "空位", status_style),
                _text_cell(f"E{row_number}", sample_name),
                _date_cell(f"F{row_number}", stored_date),
                _text_cell(f"G{row_number}", _record_value(record, "experiment_id")),
                _text_cell(f"H{row_number}", _record_value(record, "sample_type")),
            ]
            count = _record_value(record, "sample_count")
            cells.append(
                _number_cell(f"I{row_number}", int(count))
                if re.fullmatch(r"\d+", count)
                else _text_cell(f"I{row_number}", count)
            )
            cells.extend(
                [
                    _date_cell(f"J{row_number}", _record_value(record, "sample_date")),
                    _text_cell(f"K{row_number}", _record_value(record, "stored_by")),
                    _text_cell(f"L{row_number}", _record_value(record, "claimed_by")),
                    _date_cell(f"M{row_number}", _record_value(record, "claimed_date")),
                    _text_cell(f"N{row_number}", _record_value(record, "claimed_amount")),
                    _text_cell(f"O{row_number}", _record_value(record, "notes")),
                ]
            )
            rows.append(f'<row r="{row_number}" ht="22" customHeight="1">{"".join(cells)}</row>')
            row_number += 1

    widths = (12, 8, 8, 11, 22, 14, 22, 16, 12, 14, 13, 13, 14, 15, 32)
    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, 1)
    )
    last_row = storage_columns * storage_layers + 1
    return _worksheet_xml(
        dimension=f"A1:O{last_row}",
        columns=columns,
        rows="".join(rows),
        freeze='<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>',
        auto_filter=f'<autoFilter ref="A1:O{last_row}"/>',
        page_setup='<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>',
    )


def _summary_sheet(freezers: list[tuple[str, str, Mapping[str, RecordLike], int, int]]) -> str:
    used_counts = [sum(bool(record.occupied) for record in records.values()) for _name, _sheet, records, _columns, _layers in freezers]
    capacities = [columns * layers for _name, _sheet, _records, columns, layers in freezers]
    used_total = sum(used_counts)
    total_capacity = sum(capacities)
    rows = [
        '<row r="1" ht="36" customHeight="1">'
        + _text_cell("A1", "液氮罐盒位总览", 1)
        + "</row>",
        '<row r="3" ht="25" customHeight="1">'
        + "".join(
            _text_cell(f"{column}3", value, 2)
            for column, value in zip("ABCDE", ("序号", "液氮罐名称", "已使用", "剩余空位", "使用率"))
        )
        + "</row>",
    ]
    for index, ((name, sheet_name, _records, _columns, _layers), used, capacity) in enumerate(zip(freezers, used_counts, capacities), 1):
        row_number = index + 3
        escaped_sheet_name = sheet_name.replace("'", "''")
        last_row = capacity + 1
        rows.append(
            f'<row r="{row_number}" ht="24" customHeight="1">'
            + _number_cell(f"A{row_number}", index, 9)
            + _text_cell(f"B{row_number}", name, 8)
            + _formula_cell(
                f"C{row_number}",
                f'COUNTIF(\'{escaped_sheet_name}\'!$D$2:$D${last_row},"已占用")',
                used,
                9,
            )
            + _formula_cell(f"D{row_number}", f"{capacity}-C{row_number}", capacity - used, 9)
            + _formula_cell(f"E{row_number}", f"C{row_number}/{capacity}", used / capacity, 10)
            + "</row>"
        )
    total_row = len(freezers) + 5
    rows.append(
        f'<row r="{total_row}" ht="28" customHeight="1">'
        + _text_cell(f"A{total_row}", "合计", 11)
        + _text_cell(f"B{total_row}", f"共 {len(freezers)} 个液氮罐", 11)
        + _formula_cell(f"C{total_row}", f"SUM(C4:C{len(freezers) + 3})", used_total, 12)
        + _formula_cell(f"D{total_row}", f"SUM(D4:D{len(freezers) + 3})", total_capacity - used_total, 12)
        + _formula_cell(f"E{total_row}", f"C{total_row}/{total_capacity}", used_total / total_capacity, 13)
        + "</row>"
    )
    export_row = total_row + 2
    rows.append(
        f'<row r="{export_row}" ht="22" customHeight="1">'
        + _text_cell(f"A{export_row}", "导出时间", 8)
        + _text_cell(f"B{export_row}", datetime.now().strftime("%Y-%m-%d %H:%M"), 3)
        + "</row>"
    )
    return _worksheet_xml(
        dimension=f"A1:E{export_row}",
        columns=(
            '<col min="1" max="1" width="13" customWidth="1"/>'
            '<col min="2" max="2" width="18" customWidth="1"/>'
            '<col min="3" max="5" width="16" customWidth="1"/>'
        ),
        rows="".join(rows),
        merges='<mergeCells count="1"><mergeCell ref="A1:E1"/></mergeCells>',
    )


def _instructions_sheet(freezer_count: int) -> str:
    contents = (
        ("使用说明", "本文件由液氮罐管理程序生成。"),
        ("编号规则", "盒位编号由“列 + 层”组成，两位数字分别使用1–9。"),
        ("编号示例", "41 = 第4列、第1层，对应一个冻存盒位。"),
        ("工作表结构", f"本次导出包含 {freezer_count} 个液氮罐，每个液氮罐对应一个独立工作表。"),
        ("台账范围", "盒位台账按各液氮罐导出时的列数与层数列出空闲和占用盒位。"),
        ("必填字段", "样品名称和入库日期为必填；其他样品信息可按实际情况填写。"),
        ("筛选方法", "点击台账首行的筛选箭头，可按状态、位置、实验编号或人员筛选。"),
        ("日期格式", "可识别 YYYY-MM-DD 和 YYYYMMDD，导出后按 Excel 日期保存。"),
    )
    rows = [
        '<row r="1" ht="36" customHeight="1">'
        + _text_cell("A1", "液氮罐台账 · 使用说明", 1)
        + "</row>"
    ]
    for row_number, (label, value) in enumerate(contents, 3):
        rows.append(
            f'<row r="{row_number}" ht="30" customHeight="1">'
            + _text_cell(f"A{row_number}", label, 8)
            + _text_cell(f"B{row_number}", value, 3)
            + "</row>"
        )
    return _worksheet_xml(
        dimension="A1:B10",
        columns=(
            '<col min="1" max="1" width="18" customWidth="1"/>'
            '<col min="2" max="2" width="72" customWidth="1"/>'
        ),
        rows="".join(rows),
        merges='<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>',
    )


def _worksheet_xml(
    *,
    dimension: str,
    columns: str,
    rows: str,
    freeze: str = "",
    auto_filter: str = "",
    merges: str = "",
    page_setup: str = "",
) -> str:
    sheet_view = (
        '<sheetViews><sheetView showGridLines="0" workbookViewId="0">'
        + freeze
        + "</sheetView></sheetViews>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<dimension ref="{dimension}"/>{sheet_view}<sheetFormatPr defaultRowHeight="20"/>'
        f"<cols>{columns}</cols><sheetData>{rows}</sheetData>{auto_filter}{merges}{page_setup}"
        "</worksheet>"
    )


def _styles_xml() -> str:
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
  <numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd"/><numFmt numFmtId="165" formatCode="0.0%"/></numFmts>
  <fonts count="5">
    <font><sz val="10"/><name val="Microsoft YaHei"/><family val="2"/></font>
    <font><b/><sz val="16"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>
    <font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>
    <font><b/><sz val="10"/><color rgb="FF13795B"/><name val="Microsoft YaHei"/></font>
    <font><sz val="10"/><color rgb="FF7A8495"/><name val="Microsoft YaHei"/></font>
  </fonts>
  <fills count="8">
    <fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF12243A"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FF3478F6"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFE8F6F0"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF1F4F8"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFF7F9FC"/><bgColor indexed="64"/></patternFill></fill>
    <fill><patternFill patternType="solid"><fgColor rgb="FFEAF0FF"/><bgColor indexed="64"/></patternFill></fill>
  </fills>
  <borders count="3">
    <border><left/><right/><top/><bottom/><diagonal/></border>
    <border><left/><right/><top/><bottom style="thin"><color rgb="FFDDE3EC"/></bottom><diagonal/></border>
    <border><left/><right/><top/><bottom style="medium"><color rgb="FF3478F6"/></bottom><diagonal/></border>
  </borders>
  <cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>
  <cellXfs count="15">
    <xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment vertical="center"/></xf>
    <xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="164" fontId="0" fillId="0" borderId="1" xfId="0" applyNumberFormat="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="3" fillId="4" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="4" fillId="5" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="0" fillId="6" borderId="1" xfId="0" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="165" fontId="0" fillId="6" borderId="1" xfId="0" applyNumberFormat="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="2" xfId="0" applyFont="1" applyFill="1" applyBorder="1"/>
    <xf numFmtId="0" fontId="2" fillId="3" borderId="2" xfId="0" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="165" fontId="2" fillId="3" borderId="2" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center"/></xf>
    <xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1" applyAlignment="1"><alignment horizontal="center" vertical="center"/></xf>
  </cellXfs>
  <cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>
</styleSheet>'''


def _safe_sheet_names(names: Sequence[str]) -> list[str]:
    result: list[str] = []
    used: set[str] = {"汇总统计".casefold(), "使用说明".casefold()}
    for index, original in enumerate(names, 1):
        base = re.sub(r"[\\/*?:\[\]]", "_", original).strip().strip("'")
        base = base or f"液氮罐 {index}"
        base = base[:31]
        candidate = base
        suffix_number = 2
        while candidate.casefold() in used:
            suffix = f" ({suffix_number})"
            candidate = base[: 31 - len(suffix)] + suffix
            suffix_number += 1
        used.add(candidate.casefold())
        result.append(candidate)
    return result


def export_freezer_workbook(
    path: Path,
    freezers: Sequence[
        tuple[str, Mapping[str, RecordLike]]
        | tuple[str, Mapping[str, RecordLike], int]
        | tuple[str, Mapping[str, RecordLike], int, int]
    ],
) -> None:
    """Create one worksheet per freezer, plus summary and instructions sheets."""
    if not freezers:
        raise ValueError("至少需要一个液氮罐才能导出。")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    normalized = []
    for item in freezers:
        coordinates = [
            (int(str(code)[0]), int(str(code)[1]))
            for code in item[1]
            if re.fullmatch(r"[1-9][1-9]", str(code))
        ]
        columns = int(item[2]) if len(item) >= 3 else max(4, max((value[0] for value in coordinates), default=4))
        layers = int(item[3]) if len(item) == 4 else 5
        normalized.append((item[0], item[1], columns, layers))
    freezer_names = [name for name, _records, _columns, _layers in normalized]
    sheet_names = _safe_sheet_names(freezer_names)
    prepared = [
        (name, sheet_name, records, columns, layers)
        for (name, records, columns, layers), sheet_name in zip(normalized, sheet_names)
    ]
    sheets: list[tuple[str, str]] = [("汇总统计", _summary_sheet(prepared))]
    sheets.extend(
        (sheet_name, _ledger_sheet(records, columns, layers))
        for _name, sheet_name, records, columns, layers in prepared
    )
    sheets.append(("使用说明", _instructions_sheet(len(prepared))))
    worksheet_count = len(sheets)

    worksheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, worksheet_count + 1)
    )
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _xml) in enumerate(sheets, 1)
    )
    workbook_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, worksheet_count + 1)
    )

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr(
            "[Content_Types].xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
{worksheet_overrides}
<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>
<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>
<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>
</Types>''',
        )
        book.writestr(
            "_rels/.rels",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>
<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>
</Relationships>''',
        )
        book.writestr(
            "docProps/core.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
<dc:title>液氮罐冻存盒台账</dc:title><dc:creator>液氮罐管理程序</dc:creator><cp:lastModifiedBy>液氮罐管理程序</cp:lastModifiedBy>
<dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().replace(microsecond=0).isoformat()}Z</dcterms:created>
</cp:coreProperties>''',
        )
        book.writestr(
            "docProps/app.xml",
            '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>液氮罐管理</Application></Properties>''',
        )
        book.writestr(
            "xl/workbook.xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets>{workbook_sheets}</sheets><calcPr calcId="191029" calcMode="auto" fullCalcOnLoad="1"/></workbook>''',
        )
        book.writestr(
            "xl/_rels/workbook.xml.rels",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
{workbook_relationships}
<Relationship Id="rId{worksheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>''',
        )
        book.writestr("xl/styles.xml", _styles_xml())
        for index, (_name, worksheet_xml) in enumerate(sheets, 1):
            book.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml)


def _tube_ledger_sheet(samples: Mapping[tuple[str, str], BoxSampleLike]) -> str:
    header_cells = "".join(
        _text_cell(f"{_column_name(column)}1", header, 2)
        for column, header in enumerate(TUBE_HEADERS, 1)
    )
    rows = [f'<row r="1" ht="28" customHeight="1">{header_cells}</row>']
    sorted_samples = sorted(
        (
            (str(box_code), str(position), sample)
            for (box_code, position), sample in samples.items()
            if sample.occupied
        ),
        key=lambda item: (*_tube_box_sort_key(item[0]), *_tube_position_sort_key(item[1])),
    )
    for row_number, (box_code, position, sample) in enumerate(sorted_samples, 2):
        column = int(box_code[0]) if len(box_code) == 2 and box_code.isdigit() else 0
        layer = int(box_code[1]) if len(box_code) == 2 and box_code.isdigit() else 0
        cells = [
            _text_cell(f"A{row_number}", box_code),
            _number_cell(f"B{row_number}", column),
            _number_cell(f"C{row_number}", layer),
            _text_cell(f"D{row_number}", position),
            _text_cell(f"E{row_number}", f"{box_code}/{position}"),
            _text_cell(f"F{row_number}", sample.sample_name),
            _text_cell(f"G{row_number}", sample.sample_type),
            _date_cell(f"H{row_number}", sample.stored_date),
            _text_cell(f"I{row_number}", sample.stored_by.strip() or "未登记（历史）"),
            _text_cell(f"J{row_number}", sample.notes),
        ]
        rows.append(f'<row r="{row_number}" ht="22" customHeight="1">{"".join(cells)}</row>')

    if not sorted_samples:
        rows.append(
            '<row r="2" ht="28" customHeight="1">'
            + _text_cell("A2", "当前液氮罐暂无已录入的冻存管", 7)
            + "</row>"
        )
    last_row = max(2, len(sorted_samples) + 1)
    widths = (12, 8, 8, 12, 15, 32, 16, 14, 13, 38)
    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, 1)
    )
    return _worksheet_xml(
        dimension=f"A1:J{last_row}",
        columns=columns,
        rows="".join(rows),
        freeze='<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>',
        auto_filter=f'<autoFilter ref="A1:J{last_row}"/>',
        page_setup='<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>',
    )


def _tube_box_sort_key(code: str) -> tuple[int, int]:
    return (
        int(code[0]) if len(code) == 2 and code.isdigit() else 99,
        int(code[1]) if len(code) == 2 and code.isdigit() else 99,
    )


def _tube_position_sort_key(position: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Z]+)([1-9][0-9]*)", position.upper())
    if not match:
        return 999, 999
    row = 0
    for char in match.group(1):
        row = row * 26 + ord(char) - 64
    return row, int(match.group(2))


def _tube_summary_sheet(
    freezers: list[tuple[str, str, Mapping[tuple[str, str], BoxSampleLike], int]],
) -> str:
    used_counts = [
        sum(sample.occupied for sample in samples.values())
        for _name, _sheet, samples, _capacity in freezers
    ]
    capacities = [capacity for _name, _sheet, _samples, capacity in freezers]
    used_total = sum(used_counts)
    total_capacity = sum(capacities)
    rows = [
        '<row r="1" ht="36" customHeight="1">' + _text_cell("A1", "液氮罐冻存管库存总览", 1) + "</row>",
        '<row r="3" ht="25" customHeight="1">'
        + "".join(
            _text_cell(f"{column}3", value, 2)
            for column, value in zip("ABCDEF", ("序号", "液氮罐名称", "已用冻存管", "总管位", "剩余管位", "使用率"))
        )
        + "</row>",
    ]
    for index, ((name, sheet_name, _samples, capacity), used) in enumerate(zip(freezers, used_counts), 1):
        row_number = index + 3
        escaped_sheet_name = sheet_name.replace("'", "''")
        last_tube_row = max(2, used + 1)
        rows.append(
            f'<row r="{row_number}" ht="24" customHeight="1">'
            + _number_cell(f"A{row_number}", index, 9)
            + _text_cell(f"B{row_number}", name, 8)
            + _formula_cell(f"C{row_number}", f"COUNTA('{escaped_sheet_name}'!$E$2:$E${last_tube_row})", used, 9)
            + _number_cell(f"D{row_number}", capacity, 9)
            + _formula_cell(f"E{row_number}", f"D{row_number}-C{row_number}", capacity - used, 9)
            + _formula_cell(f"F{row_number}", f"IF(D{row_number}=0,0,C{row_number}/D{row_number})", used / capacity if capacity else 0, 10)
            + "</row>"
        )
    total_row = len(freezers) + 5
    first_row = 4
    last_data_row = len(freezers) + 3
    rows.append(
        f'<row r="{total_row}" ht="28" customHeight="1">'
        + _text_cell(f"A{total_row}", "合计", 11)
        + _text_cell(f"B{total_row}", f"共 {len(freezers)} 个液氮罐", 11)
        + _formula_cell(f"C{total_row}", f"SUM(C{first_row}:C{last_data_row})", used_total, 12)
        + _formula_cell(f"D{total_row}", f"SUM(D{first_row}:D{last_data_row})", total_capacity, 12)
        + _formula_cell(f"E{total_row}", f"SUM(E{first_row}:E{last_data_row})", total_capacity - used_total, 12)
        + _formula_cell(f"F{total_row}", f"IF(D{total_row}=0,0,C{total_row}/D{total_row})", used_total / total_capacity if total_capacity else 0, 13)
        + "</row>"
    )
    export_row = total_row + 2
    rows.append(
        f'<row r="{export_row}" ht="22" customHeight="1">'
        + _text_cell(f"A{export_row}", "导出时间", 8)
        + _text_cell(f"B{export_row}", datetime.now().strftime("%Y-%m-%d %H:%M"), 3)
        + "</row>"
    )
    return _worksheet_xml(
        dimension=f"A1:F{export_row}",
        columns=(
            '<col min="1" max="1" width="13" customWidth="1"/>'
            '<col min="2" max="2" width="18" customWidth="1"/>'
            '<col min="3" max="6" width="16" customWidth="1"/>'
        ),
        rows="".join(rows),
        merges='<mergeCells count="1"><mergeCell ref="A1:F1"/></mergeCells>',
    )


def _tube_instructions_sheet(freezer_count: int) -> str:
    contents = [
        ("使用说明", "本文件由液氮罐管理程序按冻存管级别生成。"),
        ("位置结构", "每条记录由液氮罐、冻存盒位和盒内孔位共同定位。"),
        ("盒位示例", "41 = 第4列、第1层，对应一个细胞冻存盒。"),
        ("孔位示例", "41/A2 = 盒位41中的A2孔位，对应一支冻存管。"),
        ("工作表结构", f"本次导出包含 {freezer_count} 个液氮罐，每个液氮罐对应一个独立工作表。"),
        ("导出范围", "液氮罐工作表只列出已录入的冻存管；空管位不会逐行输出。"),
        ("数量规则", "每个盒内孔位固定对应1支冻存管，因此台账不再设置数量列。"),
        ("必填规则", "细胞名称和入库人为必填项；导入时缺少任一项都会暂停导入。"),
        ("容量口径", "汇总页总管位按每个冻存盒的实际布局计算，未设置时默认9×9。"),
        ("规格规则", "液氮罐默认4列×5层；每个液氮罐可独立调整为1–9列×1–9层。"),
        ("筛选方法", "点击工作表首行的筛选箭头，可按盒位、孔位、细胞名称、日期或人员筛选。"),
    ]
    rows = ['<row r="1" ht="36" customHeight="1">' + _text_cell("A1", "液氮罐冻存管台账 · 使用说明", 1) + "</row>"]
    for row_number, (label, value) in enumerate(contents, 3):
        rows.append(
            f'<row r="{row_number}" ht="30" customHeight="1">'
            + _text_cell(f"A{row_number}", label, 8)
            + _text_cell(f"B{row_number}", value, 3)
            + "</row>"
        )
    last_row = len(contents) + 2
    return _worksheet_xml(
        dimension=f"A1:B{last_row}",
        columns=(
            '<col min="1" max="1" width="18" customWidth="1"/>'
            '<col min="2" max="2" width="82" customWidth="1"/>'
        ),
        rows="".join(rows),
        merges='<mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells>',
    )


def _event_ledger_sheet(events: Sequence[InventoryEventLike]) -> str:
    title = _text_cell("A1", "细胞出入库登记表", 14)
    header_cells = "".join(
        _text_cell(f"{_column_name(column)}3", header, 2)
        for column, header in enumerate(EVENT_HEADERS, 1)
    )
    rows = [
        f'<row r="1" ht="36" customHeight="1">{title}</row>',
        f'<row r="3" ht="28" customHeight="1">{header_cells}</row>',
    ]
    action_labels = {
        "IN": "入库", "OUT": "出库", "CLEAR": "清除",
        "IN_MOVE": "入库（快捷移动）", "IN_MOVE_UNDO": "入库（撤销移动）",
        "ADJUST": "信息更正",
        "HISTORY": "历史迁入",
    }
    ordered_events = list(events)
    for sequence, event in enumerate(ordered_events, 1):
        row_number = sequence + 3
        location = f"{event.freezer_name} / {event.unit_code}/{event.position}"
        if event.target_position:
            location += f" → {event.target_unit_code or event.unit_code}/{event.target_position}"
        cells = (
            _number_cell(f"A{row_number}", sequence, 4)
            + _date_cell(f"B{row_number}", event.operation_date)
            + _text_cell(f"C{row_number}", event.sample_name)
            + _date_cell(f"D{row_number}", event.frozen_date)
            + _text_cell(f"E{row_number}", location)
            + _text_cell(f"F{row_number}", action_labels.get(event.action, event.action), 4)
            + _number_cell(f"G{row_number}", 1, 4)
            + _text_cell(f"H{row_number}", event.operator, 4)
            + _text_cell(f"I{row_number}", "", 4)
            + _text_cell(f"J{row_number}", event.notes)
        )
        rows.append(f'<row r="{row_number}" ht="24" customHeight="1">{cells}</row>')
    merges = '<mergeCells count="1"><mergeCell ref="A1:J1"/></mergeCells>'
    if not ordered_events:
        rows.append('<row r="4" ht="28" customHeight="1">' + _text_cell("A4", "当前暂无出入库登记", 7) + "</row>")
        merges = '<mergeCells count="2"><mergeCell ref="A1:J1"/><mergeCell ref="A4:J4"/></mergeCells>'
    last_row = max(4, len(ordered_events) + 3)
    widths = (8, 14, 24, 14, 34, 20, 12, 14, 14, 36)
    columns = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, 1)
    )
    return _worksheet_xml(
        dimension=f"A1:J{last_row}",
        columns=columns,
        rows="".join(rows),
        freeze='<pane ySplit="3" topLeftCell="A4" activePane="bottomLeft" state="frozen"/>',
        auto_filter=f'<autoFilter ref="A3:J{last_row}"/>',
        merges=merges,
        page_setup='<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>',
    )


def export_cryotube_workbook(
    path: Path,
    freezers: Sequence[tuple[str, Mapping[tuple[str, str], BoxSampleLike], int]],
) -> None:
    """Export the current liquid-nitrogen data model: one row per cryotube."""
    if not freezers:
        raise ValueError("至少需要一个液氮罐才能导出。")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    sheet_names = _safe_sheet_names([name for name, _samples, _capacity in freezers])
    prepared = [
        (name, sheet_name, samples, max(0, int(capacity)))
        for (name, samples, capacity), sheet_name in zip(freezers, sheet_names)
    ]
    sheets: list[tuple[str, str]] = [("汇总统计", _tube_summary_sheet(prepared))]
    sheets.extend((sheet_name, _tube_ledger_sheet(samples)) for _name, sheet_name, samples, _capacity in prepared)
    sheets.append(("使用说明", _tube_instructions_sheet(len(prepared))))
    _write_workbook(path, sheets, "液氮罐冻存管台账")


def export_inventory_events_workbook(
    path: Path,
    events: Sequence[InventoryEventLike],
) -> None:
    """Export the formal registry containing ordinary inbound/outbound rows only."""
    visible_events = [event for event in events if event.action in {"IN", "OUT"}]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_workbook(
        path,
        [("出入库登记", _event_ledger_sheet(visible_events))],
        "液氮罐出入库登记",
    )


def _write_workbook(path: Path, sheets: Sequence[tuple[str, str]], title: str) -> None:
    worksheet_count = len(sheets)
    worksheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{index}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for index in range(1, worksheet_count + 1)
    )
    workbook_sheets = "".join(
        f'<sheet name="{escape(name)}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (name, _xml) in enumerate(sheets, 1)
    )
    workbook_relationships = "".join(
        f'<Relationship Id="rId{index}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{index}.xml"/>'
        for index in range(1, worksheet_count + 1)
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as book:
        book.writestr(
            "[Content_Types].xml",
            f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>{worksheet_overrides}<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>''',
        )
        book.writestr("_rels/.rels", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>''')
        book.writestr("docProps/core.xml", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{escape(title)}</dc:title><dc:creator>液氮罐管理程序</dc:creator><cp:lastModifiedBy>液氮罐管理程序</cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">{datetime.utcnow().replace(microsecond=0).isoformat()}Z</dcterms:created></cp:coreProperties>''')
        book.writestr("docProps/app.xml", '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>液氮罐管理</Application></Properties>''')
        book.writestr("xl/workbook.xml", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>{workbook_sheets}</sheets><calcPr calcId="191029" calcMode="auto" fullCalcOnLoad="1"/></workbook>''')
        book.writestr("xl/_rels/workbook.xml.rels", f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{workbook_relationships}<Relationship Id="rId{worksheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        book.writestr("xl/styles.xml", _styles_xml())
        for index, (_name, worksheet_xml) in enumerate(sheets, 1):
            book.writestr(f"xl/worksheets/sheet{index}.xml", worksheet_xml)


def _column_index(reference: str) -> int:
    match = re.match(r"[A-Za-z]+", reference)
    if not match:
        return 0
    result = 0
    for char in match.group(0).upper():
        result = result * 26 + ord(char) - 64
    return result - 1


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.parse(book.open("xl/sharedStrings.xml")).getroot()
    return [
        "".join(node.text or "" for node in item.findall(".//x:t", NS))
        for item in root.findall("x:si", NS)
    ]


def _import_cell_value(cell: ET.Element, shared: list[str]) -> object:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", NS))
    value_node = cell.find("x:v", NS)
    raw = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    if cell_type in {"str", "e"}:
        return raw
    if cell_type == "b":
        return raw == "1"
    if not raw:
        return ""
    try:
        number = float(raw)
        return int(number) if number.is_integer() else number
    except ValueError:
        return raw


def _sheet_rows(book: zipfile.ZipFile, path: str, shared: list[str]) -> list[dict[int, object]]:
    root = ET.parse(book.open(path)).getroot()
    result: list[dict[int, object]] = []
    for row in root.findall(".//x:sheetData/x:row", NS):
        values: dict[int, object] = {}
        for cell in row.findall("x:c", NS):
            values[_column_index(cell.attrib.get("r", "A1"))] = _import_cell_value(cell, shared)
        result.append(values)
    return result


def _import_text(value: object, *, as_date: bool = False) -> str:
    if value is None or value == "":
        return ""
    if as_date and isinstance(value, (int, float)):
        try:
            return (date(1899, 12, 30) + timedelta(days=int(value))).isoformat()
        except (OverflowError, ValueError):
            pass
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def preview_freezer_workbook(path: Path) -> ImportPreview:
    """Parse and validate an import workbook without changing local data."""
    path = Path(path)
    if not path.exists():
        raise ValueError("找不到所选 Excel 文件。")
    issues: list[ImportIssue] = []
    try:
        with zipfile.ZipFile(path) as book:
            shared = _shared_strings(book)
            workbook_root = ET.parse(book.open("xl/workbook.xml")).getroot()
            rels_root = ET.parse(book.open("xl/_rels/workbook.xml.rels")).getroot()
            relationships = {
                item.attrib.get("Id", ""): item.attrib.get("Target", "")
                for item in rels_root.findall("pr:Relationship", NS)
            }
            sheet_entries: list[tuple[str, str]] = []
            for sheet in workbook_root.findall("x:sheets/x:sheet", NS):
                name = sheet.attrib.get("name", "未命名液氮罐")
                relation_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id", "")
                target = relationships.get(relation_id, "")
                if not target:
                    continue
                normalized = target.lstrip("/")
                if not normalized.startswith("xl/"):
                    normalized = posixpath.normpath(posixpath.join("xl", normalized))
                sheet_entries.append((name, normalized))

            original_names: list[str] = []
            for name, sheet_path in sheet_entries:
                if name != "汇总统计":
                    continue
                for values in _sheet_rows(book, sheet_path, shared):
                    sequence = values.get(0)
                    freezer_name = _import_text(values.get(1, ""))
                    if isinstance(sequence, (int, float)) and freezer_name:
                        original_names.append(freezer_name)

            ledger_entries = [
                (name, sheet_path)
                for name, sheet_path in sheet_entries
                if name not in {"汇总统计", "出入库记录", "使用说明"}
            ]
            imported: list[tuple[str, dict[str, dict[str, str]]]] = []
            field_headers = {
                "样品名称": "sample_name",
                "入库日期": "stored_date",
                "实验编号": "experiment_id",
                "样品类别": "sample_type",
                "样品个数": "sample_count",
                "采样日期": "sample_date",
                "入库人": "stored_by",
                "领用人": "claimed_by",
                "领用日期": "claimed_date",
                "领用量": "claimed_amount",
                "备注": "notes",
            }
            seen_experiments: dict[str, tuple[str, int]] = {}
            for freezer_index, (sheet_name, sheet_path) in enumerate(ledger_entries):
                rows = _sheet_rows(book, sheet_path, shared)
                if not rows:
                    issues.append(ImportIssue("错误", sheet_name, 1, "工作表", "", "工作表没有任何内容。"))
                    continue
                headers = {_import_text(value): column for column, value in rows[0].items()}
                tube_format = {"冻存盒位", "盒内孔位", "细胞名称"}.issubset(headers)
                if tube_format:
                    records: dict[str, dict[str, str]] = {}
                    for row_number, values in enumerate(rows[1:], 2):
                        box_code = _import_text(values.get(headers["冻存盒位"], ""))
                        position = _import_text(values.get(headers["盒内孔位"], "")).upper()
                        sample_name = _import_text(values.get(headers["细胞名称"], ""))
                        sample_type = _import_text(values.get(headers.get("细胞类别", -1), ""))
                        stored_date = _import_text(
                            values.get(headers.get("入库日期", -1), ""),
                            as_date=True,
                        )
                        stored_by = _import_text(values.get(headers.get("入库人", -1), ""))
                        notes = _import_text(values.get(headers.get("备注", -1), ""))
                        has_sample_data = any(
                            (sample_name, sample_type, stored_date, stored_by, notes)
                        )
                        if (
                            box_code.startswith("当前液氮罐暂无")
                            and not position
                            and not has_sample_data
                        ):
                            continue
                        if not box_code and not position and not has_sample_data:
                            continue
                        if not re.fullmatch(r"[1-9][1-9]", box_code):
                            issues.append(
                                ImportIssue(
                                    "错误",
                                    sheet_name,
                                    row_number,
                                    "冻存盒位",
                                    box_code,
                                    "冻存盒位必须是两位有效编码，例如 41。",
                                )
                            )
                            continue
                        if not re.fullmatch(r"[A-Z]+[1-9][0-9]*", position):
                            issues.append(
                                ImportIssue(
                                    "错误",
                                    sheet_name,
                                    row_number,
                                    "盒内孔位",
                                    position,
                                    "盒内孔位必须使用字母加数字，例如 A2。",
                                )
                            )
                            continue
                        if not sample_name:
                            issues.append(
                                ImportIssue(
                                    "错误",
                                    sheet_name,
                                    row_number,
                                    "细胞名称",
                                    "",
                                    "该行已有位置信息，但必填的细胞名称为空。",
                                )
                            )
                            continue
                        if not stored_by:
                            issues.append(
                                ImportIssue(
                                    "错误",
                                    sheet_name,
                                    row_number,
                                    "入库人",
                                    "",
                                    "该行已有细胞信息，但必填的入库人为空。",
                                )
                            )
                            continue
                        if stored_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", stored_date):
                            issues.append(
                                ImportIssue(
                                    "提醒",
                                    sheet_name,
                                    row_number,
                                    "入库日期",
                                    stored_date,
                                    "日期建议使用 YYYY-MM-DD 或 YYYYMMDD。",
                                )
                            )
                        full_position = f"{box_code}/{position}"
                        if full_position in records:
                            issues.append(
                                ImportIssue(
                                    "错误",
                                    sheet_name,
                                    row_number,
                                    "完整位置",
                                    full_position,
                                    "同一工作表中出现重复冻存管位置。",
                                )
                            )
                            continue
                        records[full_position] = {
                            "sample_name": sample_name,
                            "quantity": "1",
                            "sample_id": "",
                            "sample_type": sample_type,
                            "stored_date": stored_date,
                            "stored_by": stored_by,
                            "notes": notes,
                        }
                    freezer_name = (
                        original_names[freezer_index]
                        if freezer_index < len(original_names)
                        else sheet_name
                    )
                    imported.append((freezer_name, records))
                    continue
                code_header = "盒位编号" if "盒位编号" in headers else "单元编号"
                if code_header not in headers or not ({"样品名称", "实验编号"} & headers.keys()):
                    issues.append(
                        ImportIssue(
                            "错误",
                            sheet_name,
                            1,
                            "列标题",
                            "",
                            "缺少“盒位编号”以及可识别的样品名称列，请使用本程序导出的模板。",
                        )
                    )
                    continue
                records: dict[str, dict[str, str]] = {}
                modern_format = "样品名称" in headers
                for row_number, values in enumerate(rows[1:], 2):
                    code = _import_text(values.get(headers[code_header], ""))
                    sample_name = _import_text(values.get(headers.get("样品名称", -1), ""))
                    stored_date = _import_text(values.get(headers.get("入库日期", -1), ""), as_date=True)
                    experiment = _import_text(values.get(headers.get("实验编号", -1), ""))
                    has_other_data = any(
                        _import_text(values.get(headers[header], ""))
                        for header in field_headers
                        if header in headers
                    )
                    if not code and not sample_name and not experiment and not has_other_data:
                        continue
                    if not re.fullmatch(r"[1-9][1-9]", code):
                        issues.append(ImportIssue("错误", sheet_name, row_number, "盒位编号", code, "盒位编码必须是两位有效编码，例如 41。"))
                        continue
                    if modern_format and not sample_name:
                        if has_other_data:
                            issues.append(ImportIssue("错误", sheet_name, row_number, "样品名称", "", "该行填写了样品信息，但必填的样品名称为空。"))
                        continue
                    if not sample_name:
                        sample_name = experiment
                    if not sample_name:
                        if has_other_data:
                            issues.append(ImportIssue("错误", sheet_name, row_number, "样品名称", "", "无法识别该行的样品名称。"))
                        continue
                    if modern_format and not stored_date:
                        issues.append(ImportIssue("错误", sheet_name, row_number, "入库日期", "", "已填写样品名称，但必填的入库日期为空。"))
                        continue
                    if not modern_format and not stored_date:
                        stored_date = _import_text(values.get(headers.get("采样日期", -1), ""), as_date=True)
                        issues.append(ImportIssue("提醒", sheet_name, row_number, "入库日期", stored_date, "旧版台账没有入库日期列，已使用采样日期或留空；导入后可继续补充。"))
                    if code in records:
                        issues.append(ImportIssue("错误", sheet_name, row_number, "单元编号", code, "同一工作表中出现重复储位编码。"))
                        continue
                    record: dict[str, str] = {
                        "sample_name": sample_name,
                        "stored_date": stored_date,
                        "experiment_id": experiment,
                    }
                    for header, field in field_headers.items():
                        if header in {"样品名称", "入库日期", "实验编号"} or header not in headers:
                            continue
                        record[field] = _import_text(
                            values.get(headers[header], ""),
                            as_date=header in {"入库日期", "采样日期", "领用日期"},
                        )
                    for header, field in (("入库日期", "stored_date"), ("采样日期", "sample_date"), ("领用日期", "claimed_date")):
                        value = record.get(field, "")
                        if value and not re.fullmatch(r"\d{4}-\d{2}-\d{2}|\d{8}", value):
                            issues.append(ImportIssue("提醒", sheet_name, row_number, header, value, "日期建议使用 YYYY-MM-DD 或 YYYYMMDD。"))
                    previous = seen_experiments.get(experiment.casefold()) if experiment else None
                    if previous and experiment:
                        issues.append(
                            ImportIssue(
                                "提醒",
                                sheet_name,
                                row_number,
                                "实验编号",
                                experiment,
                                f"实验编号与 {previous[0]} 第 {previous[1]} 行重复，请确认是否为不同分装。",
                            )
                        )
                    elif experiment:
                        seen_experiments[experiment.casefold()] = (sheet_name, row_number)
                    records[code] = record
                freezer_name = (
                    original_names[freezer_index]
                    if freezer_index < len(original_names)
                    else sheet_name
                )
                imported.append((freezer_name, records))
    except (KeyError, ET.ParseError, zipfile.BadZipFile, OSError) as exc:
        raise ValueError("无法读取该 Excel 文件，请选择本程序导出的 .xlsx 台账。") from exc
    if not imported and not issues:
        raise ValueError("Excel 中没有找到可导入的液氮罐台账工作表。")
    return ImportPreview(imported, issues)


def import_freezer_workbook(path: Path) -> list[tuple[str, dict[str, dict[str, str]]]]:
    """Read a valid workbook exported by this application and return its records."""
    preview = preview_freezer_workbook(path)
    if preview.error_count:
        raise ValueError(f"Excel 中发现 {preview.error_count} 个错误，请先修正后再导入。")
    return preview.freezers
