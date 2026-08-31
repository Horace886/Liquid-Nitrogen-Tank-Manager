import path from "node:path";
import fs from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workDir = path.dirname(fileURLToPath(import.meta.url));
const workbook = await SpreadsheetFile.importXlsx(
  await FileBlob.load(path.join(workDir, "event_export.xlsx")),
);
console.log((await workbook.inspect({ kind: "workbook,sheet", maxChars: 4000 })).ndjson);
console.log((await workbook.inspect({
  kind: "region",
  sheetId: "出入库登记",
  range: "A1:J8",
  maxChars: 5000,
})).ndjson);
console.log((await workbook.inspect({
  kind: "region",
  sheetId: "出入库登记",
  range: "G3:J8",
  maxChars: 3000,
})).ndjson);
console.log((await workbook.inspect({
  kind: "match",
  searchTerm: "快捷移动|清除|历史迁入|信息更正",
  options: { useRegex: true, maxResults: 100 },
  summary: "excluded action scan",
})).ndjson);
console.log((await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
})).ndjson);

const inventoryWorkbook = await SpreadsheetFile.importXlsx(
  await FileBlob.load(path.join(workDir, "inventory_export.xlsx")),
);
console.log((await inventoryWorkbook.inspect({ kind: "workbook,sheet", maxChars: 4000 })).ndjson);

const eventPreview = await workbook.render({ sheetName: "出入库登记", autoCrop: "all", scale: 1.0, format: "png" });
await fs.writeFile(path.join(workDir, "event_preview.png"), new Uint8Array(await eventPreview.arrayBuffer()));
const inventoryPreview = await inventoryWorkbook.render({ sheetName: "汇总统计", autoCrop: "all", scale: 1.0, format: "png" });
await fs.writeFile(path.join(workDir, "inventory_preview.png"), new Uint8Array(await inventoryPreview.arrayBuffer()));
