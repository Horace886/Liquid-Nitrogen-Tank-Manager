<a id="chinese"></a>

# 液氮罐管理程序

[简体中文](#chinese) | [English](#english)

一个面向 Windows 的本地液氮罐库存管理工具，用于管理液氮罐、冻存盒、盒内孔位、细胞库存和出入库登记。

程序使用 Python、Tkinter 和 SQLite，数据保存在本机，不需要安装第三方 Python 依赖。界面支持简体中文和 English，下方另附英文使用说明。

### 切换界面语言

左侧底部的“语言 / Language”可切换简体中文和 English，首次默认中文，选择会保存到当前 Windows 用户的 `%LOCALAPPDATA%/LiquidNitrogenTankManager/preferences.json`。切换立即生效，不会重建当前页面；搜索条件、已打开盒子标记及当前液氮罐保持不变。

录入或其他弹窗未关闭时不切换语言，请先保存或取消。不翻译细胞名称、人员、备注或用户自定义液氮罐名称。日期仍使用 `YYYY-MM-DD`。复制或分发程序时请包含 `ui_en.json` 和两个语言界面模块。

Excel 导出跟随当前界面语言：库存台账、出入库登记表的标题、表头、系统状态、汇总页、使用说明和默认文件名均支持中英文；导入问题清单也跟随语言。导入会自动识别中英文表头，不受当前界面语言限制，继续兼容旧版中文台账；同一表内可以混用两种语言，但同一字段不可同时保留中文列和英文列。

库存导出与出入库登记导出仍然分开。正式登记只导出所选日期范围内的普通入库和出库，保留原始人员、日期、位置、细胞信息和备注，不包含快捷移动、清除或历史迁入。登记表不能作为库存台账导入。所有导入仍先校验并创建备份。

## 主要功能

- 管理多个液氮罐，并支持重命名、归档和恢复。
- 查看冻存盒位总览和使用率统计。
- 记录细胞名称、类别、实验编号、入库日期、入库人和备注。
- 在全部未归档液氮罐中搜索细胞，并定位到对应冻存盒和孔位。
- 按所需冻存管数量查找同盒连续空孔。
- 批量入库、出库、移动和清空孔位。
- 保存可筛选、可分页查看的出入库历史记录。
- 导入、导出 Excel，并在导入前自动备份数据库。
- 备份和恢复本地 SQLite 数据库。

## 运行环境

- Windows
- Python 3，建议使用 Python 3.11；Python 3.13 已通过当前自动化测试及界面样式专项检查
- Python 安装中需包含 Tkinter；从 [Python 官方网站](https://www.python.org/downloads/windows/) 安装的 Windows 版本通常默认包含

项目当前只使用 Python 标准库，不需要运行 `pip install`。

## 快速开始

### 1. 获取代码

下载 GitHub 仓库的 ZIP 文件并解压，或者使用 Git 克隆：

```powershell
git clone https://github.com/Horace886/Liquid-Nitrogen-Tank-Manager.git
cd Liquid-Nitrogen-Tank-Manager
```

### 2. 启动程序

在 Windows 中双击：

```text
start_liquid_nitrogen_tank_manager.bat
```

也可以在项目目录的 PowerShell 中运行：

```powershell
py -3 liquid_nitrogen_tank_manager.py
```

第一次启动时，程序会在项目目录创建本地数据库：

```text
liquid_nitrogen_tank_storage.db
```

## 数据安全

- `liquid_nitrogen_tank_storage.db` 是本机库存数据库，不应提交到 GitHub。
- `backups/` 保存数据库备份，不应提交到 GitHub。
- 项目根目录中的 Excel、CSV 文件可能包含真实业务数据，不应提交到 GitHub。
- 上述文件已通过 `.gitignore` 排除，但每次提交前仍应运行 `git status` 检查。
- GitHub 仓库不包含本地数据库，因此不能代替数据库备份；请另外妥善保存 `backups/`。
- 测试和开发时不要使用真实数据库造数，使用临时目录或数据库副本。

## 液氮罐和冻存盒结构

- 每个新建液氮罐默认有 4 列、每列 5 层，共 20 个冻存盒位；每个液氮罐可独立调整为 1–9 列、1–9 层。
- 盒位编号由“列 + 层”组成，例如 `41` 表示第 4 列、第 1 层。
- 扩大规格可直接保存；缩小规格前必须先移动或清空新范围以外的库存，程序不会自动搬移或删除冻存管。
- 每个盒位对应一个冻存盒；冻存盒默认布局为 9×9，可在 1×1 到 20×20 之间调整。
- 每个盒内孔位按 1 支冻存管统计。
- 孔位区域支持缩放、适应窗口和横向或纵向滚动。

## 界面与交互

- 侧栏用蓝色高亮当前页面，容量条显示当前液氮罐的冻存管使用率。
- 窗口较矮时工作台导航可滚动，键盘聚焦会自动显示对应按钮；底部容量信息保持固定。
- 总览统计卡片将数量和说明分行显示，避免小窗口下挤在一行。
- 圆角按钮提供约 100 毫秒的悬停颜色过渡，支持快速移入、移出；切页和执行操作不会等待动画。
- 侧栏按钮仅使用背景高亮，不显示额外焦点框；其他按钮键盘聚焦时显示高对比边框，输入框聚焦时显示蓝色边框。
- 冻存盒的放大、缩小和适应窗口按钮不显示额外焦点框，键盘聚焦时以背景高亮提示。

## 搜索和批量操作

普通“查找细胞”会搜索全部未归档液氮罐，并显示液氮罐、冻存盒和孔位。打开其他液氮罐中的结果时，程序会自动切换并高亮对应孔位。

包含批量移动或批量清空的高级搜索只作用于当前液氮罐，以避免跨罐误操作。离开当前冻存盒、切换液氮罐或打开其他冻存盒时，程序会自动清除批量选择和快捷移动等临时状态。

冻存盒总览和盒内批量选择支持 Windows 风格的连续选择：先点击起点，再按住 `Shift` 点击终点，即可选中界面顺序中两者之间的全部项目；批量模式状态栏会显示操作提示。选中项目使用蓝色描边，仍保留空位或已有细胞的原有底色。

“查找空位”可以在当前或全部液氮罐中推荐同盒连续空孔。打开推荐结果后，程序会高亮并选中对应孔位。

## 出入库登记

- 普通入库和普通出库保存操作人员、业务日期和系统时间。
- 清除、快捷移动、撤销移动、信息更正和历史迁入保持匿名。
- 快捷移动只产生一条“入库（快捷移动）”记录，并保留原入库信息。
- 登记列表使用 SQLite 统计和分页读取，历史记录会完整保存在数据库中。
- 列表界面最多加载最近 300 条，运行内存最多缓存最近 1000 条。

## Excel 导入导出

“导出 Excel”生成库存总台账，包括汇总统计、各液氮罐的独立工作表和使用说明，不包含正式出入库登记。

“出入库登记”页面提供独立的登记导出功能，只导出指定日期范围内的普通入库和普通出库记录。历史迁入、清除、信息更正和快捷移动不会进入正式登记表。

导入 Excel 时：

- 程序会先显示导入预览。
- 程序会在导入前自动创建数据库备份。
- 盒位编号必须为 `11`–`99` 范围内的两位有效编码；导入已有液氮罐时，位置不能超出该罐当前规格。
- 导入新液氮罐时会按库存中的最大列、层推断规格，且不低于默认的 4 列 × 5 层。
- 导入和导出操作本身不会写入出入库登记。

## 项目结构

| 路径 | 作用 |
| --- | --- |
| `liquid_nitrogen_tank_manager.py` | Tkinter 图形界面和程序入口 |
| `liquid_nitrogen_tank_store.py` | SQLite 数据存储和业务规则 |
| `liquid_nitrogen_tank_excel.py` | Excel 导入导出 |
| `start_liquid_nitrogen_tank_manager.bat` | Windows 双击启动入口 |
| `tests/` | 核心自动化测试 |
| `_event_verification/` | UI 冒烟、性能和专项验证脚本 |
| `AGENT.md` | 项目维护规则和代码说明 |
| `LICENSE` | PolyForm Noncommercial 1.0.0 完整许可证 |

## 运行测试

在项目目录的 PowerShell 中运行：

```powershell
py -3 -m unittest discover -s tests -v
```

涉及 Tkinter 界面、日期控件、键盘焦点或中文输入法行为时，可以继续运行：

```powershell
py -3 _event_verification/ui_smoke.py
```

界面样式与动画专项检查（使用临时数据）：

```powershell
py -3 _event_verification/ui_polish_smoke.py
```

添加 `--preview` 可打开 5 分钟的演示窗口，添加 `--compact` 可检查 1000×620 小窗口布局。

## 稳定性与数据安全免责声明

本软件按“现状”提供，可能存在缺陷、不稳定、兼容性问题或运行中断，不保证持续可用、运行无误或适合任何特定用途。使用本软件可能导致数据丢失、损坏、记录错误或其他数据安全问题。

使用者应自行评估风险，在正式使用前以测试数据验证功能，定期保存独立于本软件的数据库备份并验证其可恢复性。导入、恢复、批量修改或升级前应另行备份，并在操作后核对关键库存记录；请勿将本软件作为重要数据的唯一保存手段。

在适用法律允许的最大范围内，作者及其他许可方不对因使用或无法使用本软件而造成的数据丢失、损坏、泄露或其他相关损失承担责任；法律规定不得排除或限制的责任除外。

本声明为风险提示，不替代或修改 [LICENSE](LICENSE) 中的无担保及责任限制条款。

## 许可证

本项目采用 [PolyForm Noncommercial License 1.0.0](LICENSE)。

- 允许在许可证规定的非商业用途范围内使用、修改和分发软件。
- 分发时须提供完整许可条款或其官方链接，并保留原作者提供的 `Required Notice:` 声明（如有）。
- 本许可证不授予商业用途的使用权；超出许可范围的商业使用需事先另行获得权利人的授权。
- 许可证也明确允许教育机构、公共研究机构、慈善组织等所列机构使用，具体范围以原文为准。

以上仅为便于理解的摘要，完整授权条件以 [LICENSE](LICENSE) 中的英文原文为准。

---

<a id="english"></a>

# Liquid Nitrogen Tank Manager

[简体中文](#chinese) | [English](#english)

A local Windows desktop application for managing liquid nitrogen tanks, cryoboxes, individual tube positions, cell inventory, and stock-in/stock-out records.

Built with Python, Tkinter, and SQLite, it stores data locally and requires no third-party Python packages. The interface supports both Simplified Chinese and English.

### Interface language

Choose **简体中文** or **English** from **语言 / Language** at the bottom of the sidebar. Chinese is the initial default. Your preference is saved per Windows user in `%LOCALAPPDATA%/LiquidNitrogenTankManager/preferences.json`. Switching takes effect in place, preserving the current tank, search criteria and visited-box markers.

Finish or cancel open dialogs before switching. User-entered cell names, people, notes and tank names are never translated. Dates remain `YYYY-MM-DD`. Include `ui_en.json` and both language UI modules when copying or distributing the application.

Excel exports follow the current interface language, including inventory and stock-activity titles, column headings, system statuses, summary sheets, instructions and suggested filenames. Import-issue CSV reports also follow the selected language. Import automatically recognizes Chinese and English headings regardless of interface language, and remains compatible with legacy Chinese workbooks. Mixed-language headings are accepted, but duplicate columns for the same field are rejected.

Inventory and stock-activity exports remain separate. The formal register includes only ordinary stock-in and stock-out records within the selected dates; original people, dates, locations, cell information and notes are preserved. Quick moves, clearing and historical imports are excluded. A stock-activity register cannot be imported as inventory. Import validation and pre-import backups remain in place.

## Features

- Manage multiple tanks, including renaming, archiving, and restoring them.
- View cryobox locations and capacity utilization.
- Record cell names, categories, experiment IDs, storage dates, operators, and notes.
- Search for cells across all non-archived tanks and locate matching boxes and tube positions.
- Find consecutive empty positions within a box for a requested number of tubes.
- Perform batch stock-in, stock-out, moves, and position clearing.
- Keep filterable, paginated inventory event history.
- Import and export Excel workbooks, with automatic database backups before imports.
- Back up and restore the local SQLite database.

## Requirements

- Windows
- Python 3; Python 3.11 is recommended, and Python 3.13 has passed the current automated test suite and focused UI styling checks
- Tkinter must be included in the Python installation; Windows installers from the [official Python website](https://www.python.org/downloads/windows/) normally include it

The project currently uses only the Python standard library. No `pip install` step is required.

## Quick Start

### 1. Get the code

Download and extract the repository ZIP file from GitHub, or clone it with Git:

```powershell
git clone https://github.com/Horace886/Liquid-Nitrogen-Tank-Manager.git
cd Liquid-Nitrogen-Tank-Manager
```

### 2. Run the application

On Windows, double-click:

```text
start_liquid_nitrogen_tank_manager.bat
```

Alternatively, run the following command in PowerShell from the project directory:

```powershell
py -3 liquid_nitrogen_tank_manager.py
```

On first launch, the application creates a local database in the project directory:

```text
liquid_nitrogen_tank_storage.db
```

## Data Safety

- `liquid_nitrogen_tank_storage.db` contains local inventory data and must not be committed to GitHub.
- `backups/` contains database backups and must not be committed to GitHub.
- Excel and CSV files in the project root may contain real operational data and must not be committed to GitHub.
- These files are excluded through `.gitignore`, but always review `git status` before committing.
- The GitHub repository does not contain your local database and is not a substitute for database backups. Store `backups/` safely elsewhere as well.
- Use temporary directories or database copies for development and testing; never populate the real inventory database with test data.

## Tank and Cryobox Layout

- Each new tank defaults to 4 columns and 5 levels, providing 20 box locations. Each tank can independently be configured with 1–9 columns and 1–9 levels.
- A box location consists of two digits: column followed by level. For example, `41` means column 4, level 1.
- Increasing the tank dimensions can be saved directly. Before reducing them, move or clear inventory outside the new range. The application does not automatically relocate or delete tubes.
- Each box location corresponds to one cryobox. Boxes default to a 9×9 layout, adjustable from 1×1 to 20×20.
- Each occupied position represents one cryovial.
- The position grid supports zooming, fit-to-window sizing, and horizontal or vertical scrolling.

## Interface and Interaction

- The sidebar highlights the current page in blue, and the capacity bar shows tube utilization for the current tank.
- In shorter windows, the navigation area scrolls and automatically brings keyboard-focused buttons into view. Capacity information remains fixed at the bottom.
- Overview cards display counts and descriptions on separate lines to prevent crowding in small windows.
- Rounded buttons use an approximately 100-millisecond hover color transition and support rapid pointer entry and exit. Navigation and actions do not wait for animations.
- Sidebar buttons use background highlighting without an additional focus outline. Other buttons show a high-contrast border when keyboard-focused, and input fields show a blue focus border.

## Search and Batch Operations

The standard cell search (查找细胞) searches all non-archived tanks and displays the tank, box, and position for each result. Opening a result from another tank automatically switches to that tank and highlights the matching positions.

Advanced search with batch move or batch clear operations is restricted to the current tank to prevent unintended cross-tank changes. Leaving the current box, switching tanks, or opening another box clears temporary states such as batch selections and quick-move mode.

The empty-position search (查找空位) recommends consecutive empty positions within a single box, either in the current tank or across all tanks. Opening a recommendation highlights and selects the suggested positions.

## Stock-in/Stock-out Records

- Regular stock-in and stock-out operations record the operator, operation date, and system timestamp.
- Clearing, quick moves, undoing moves, corrections, and historical imports do not record an operator.
- A quick move creates only one stock-in (quick move) event and preserves the original storage information.
- The event list uses SQLite statistics and pagination. Complete history is retained in the database.
- The interface loads at most the latest 300 events, and the in-memory cache holds at most the latest 1,000 events.

## Excel Import and Export

The Export Excel action (导出 Excel) generates an inventory workbook with summary statistics, a separate worksheet for each tank, and usage instructions. It does not include the formal stock-in/stock-out register.

The Stock-in/Stock-out Records page (出入库登记) has a separate export action for regular stock-in and stock-out records within a selected date range. Historical imports, clearing, corrections, and quick moves are excluded from this formal register.

When importing Excel files:

- The application shows a preview first.
- A database backup is created automatically before the import.
- Box locations must be valid two-digit codes from `11` to `99`, with each digit between 1 and 9. Locations imported into an existing tank must fit its current dimensions.
- For a newly imported tank, dimensions are inferred from the highest column and level in the inventory, with a minimum of the default 4 columns × 5 levels.
- Import and export operations themselves do not create stock-in/stock-out events.

## Project Structure

| Path | Purpose |
| --- | --- |
| `liquid_nitrogen_tank_manager.py` | Tkinter interface and application entry point |
| `liquid_nitrogen_tank_store.py` | SQLite storage and business rules |
| `liquid_nitrogen_tank_excel.py` | Excel import and export |
| `start_liquid_nitrogen_tank_manager.bat` | Windows double-click launcher |
| `tests/` | Core automated tests |
| `_event_verification/` | UI smoke tests, performance checks, and targeted verification scripts |
| `AGENT.md` | Project maintenance rules and code guidance |
| `LICENSE` | Full PolyForm Noncommercial 1.0.0 license |

## Running Tests

Run the following command in PowerShell from the project directory:

```powershell
py -3 -m unittest discover -s tests -v
```

For changes involving Tkinter screens, date controls, keyboard focus, or Chinese input methods, also run:

```powershell
py -3 _event_verification/ui_smoke.py
```

For focused UI styling and animation checks using temporary data:

```powershell
py -3 _event_verification/ui_polish_smoke.py
```

Add `--preview` to keep a demonstration window open for five minutes. Combine it with `--compact` to inspect the 1000×620 compact layout.

## Stability and Data Safety Disclaimer

This software is provided "as is" and may contain defects, instability, compatibility issues, or service interruptions. Continuous availability, error-free operation, and suitability for any particular purpose are not guaranteed. Using the software may result in data loss, corruption, inaccurate records, or other data security issues.

Users should assess these risks, validate functionality with test data before operational use, and regularly keep independent database backups and verify that they can be restored. Create a separate backup before imports, restores, bulk changes, or upgrades, and check critical inventory records afterward. Do not rely on this software as the sole means of storing important data.

To the maximum extent permitted by applicable law, the author and other licensors are not liable for data loss, corruption, disclosure, or other related losses arising from the use of or inability to use this software. This does not exclude or limit liability that cannot lawfully be excluded or limited.

This notice highlights risks and does not replace or modify the warranty disclaimer or limitation of liability in [LICENSE](LICENSE).

## License

This project is licensed under the [PolyForm Noncommercial License 1.0.0](LICENSE).

- Use, modification, and distribution are permitted for noncommercial purposes within the scope of the license.
- When distributing the software, include the license terms or their official URL and retain any `Required Notice:` statements supplied by the licensor.
- This license does not grant permission for commercial purposes. Commercial use outside its permitted scope requires prior, separate authorization from the rights holder.
- The license also expressly permits use by listed organizations, including educational institutions, public research organizations, and charities. Refer to the full text for its scope.

This is a summary for convenience. The complete English terms in [LICENSE](LICENSE) govern.
