# Changelog

All notable changes to WWPTools will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Telemetry: unhandled command errors are now also filed (or updated) as a deduplicated GitHub issue, detected the next time Revit starts (via the existing pyRevit telemetry replay in app-init.py, not in real time) -- via a new, independent reporting endpoint separate from the existing Neon-backed usage log, so a bug reporting outage never affects usage telemetry or vice versa.
- True North Updater: can now also compute and write a Project North angle onto titleblocks in the same run, with its own optional visibility parameter -- opt-in via a new "Also update a Project North angle parameter" checkbox so existing True-North-only titleblocks and saved settings are unaffected. The existing "hide on elevation/section" option now applies to whichever of True North/Project North visibility toggles are active.

### Changed

- Bulk Import Shared Parameters: added a multi-select parameter picker and a selected-row bulk editor for Instance/Type, Parameter Group, and Category. Bulk edits fill missing values only by default, with an option to overwrite existing selected-row values.
- Import Key Schedule: added an Excel worksheet selector. The chosen sheet is remembered, and the importer requires reloading whenever the workbook path or sheet changes so mappings cannot be applied to stale data from another tab.
- Super Editor: the Preview & Apply report is now an interactive checklist instead of read-only text. Every planned change gets its own checkbox (checked by default), with Select All/Select None buttons, so you can exclude specific elements from a batch without cancelling the whole operation. Skipped (invalid/conflicting) items remain a read-only list. Family renames (from "Also rename family name") appear as their own checkable section.

### Fixed

- Error reporting service: the "match found" branch in report-error.js no longer throws if a GitHub issue's body is null/absent -- introduced a single `body` local (`existing.body || ''`) used consistently for both the marker match and the marker replace.
- app-init.py: the telemetry replay loop now respects the user's telemetry opt-out (`WWP_telemetry.is_telemetry_enabled()`) before sending the usage-log worker post or the deduplicated error report; the local debug log write remains unconditional since it never leaves the machine.
- True North Updater: the shared "hide on elevation/section" checkbox no longer skips writing an arrow's angle on elevation/section sheets when that specific arrow (True North or Project North) has no visibility parameter configured -- each arrow's hide/skip decision now gates on its own visibility toggle and parameter, not just the shared checkbox. Also added a dialog validation error if True North and Project North are set to the same target parameter, preventing one angle from silently overwriting the other.

## [2.2.0] - 2026-07-03

### Added

- Mass Stats CA: new tool under the MassStats pulldown for Canadian/Toronto projects. Reads mass floor areas from selected mass elements; auto-discovers program combinations from up to three user-selected Revit parameters (e.g. Program, Mass Category, Mass Name) whose column header labels are editable; reports GCA (raw Revit area), GFA, NSA, unit counts, population and jobs -- total and by program. Supports up to 5 grouping parameters with subtotals; repeated group values are suppressed (shown only on change) with a blank spacer row after each subtotal. Settings (ratios per classifier combination) saved to AppData XML and also embedded in the Revit project via ExtensibleStorage so they sync with the model during cloud/central worksharing.
- Mass Stats (UK): fixed numbers in the MASS SETTINGS right panel being clipped in the BoH section. Removed WPF local height/padding overrides from dynamically created TextBoxes so the FlatTheme minimum height (34px) takes effect.

- Super Renamer: renamed "Element names" option to "Element names (Views, Sheets, Rooms...)" and added hover tooltips to all Rename dropdown options so users can tell at a glance which option covers views, sheets, levels, etc.
- Super Renamer: merged "Family names" into "Type names" as an "Also rename family name" checkbox, so types and their parent family can be renamed in one pass. Family renames appear in the preview and results separately.
- Super Renamer: category dropdown now includes families that are loaded but have no placed instances (e.g. Doors, Windows), and filters out internal `<...>` Revit categories.
- Super Renamer: instance/type parameter modes now include Sheets and Views in the category list, and include Annotation categories (e.g. Title Blocks) alongside Model categories.
- Super Editor: added "Assign workset" as a new target mode -- reassign placed instances in a category (or the current selection) to a different user-created workset, with the same preview/apply flow used for renaming. Only appears in workshared documents.
- Super Editor: the preview report for parameter value changes (instance and type) and workset assignment now shows the owning element's name alongside each old -> new value, so identical value changes across many elements can be told apart.

### Changed

- Renamed "Super Renamer" to "Super Editor" (by Category / by Selections) to reflect parameter and workset editing beyond simple renaming.
- Super Editor: expanded the pulldown tooltip to describe all three capabilities (renaming, parameter editing, workset assignment) instead of just renaming.

### Fixed

- Propagate Views To Levels: fixed `ImportError: Cannot import name Process` crash when opening the level picker. The `from System.Diagnostics import Process` import was outside the try/except guarding its usage, so on environments where that import fails it crashed instead of falling back gracefully -- moved the import inside the try block, matching the pattern already used in `WWP_uiUtils._get_owner_handle()`.
- Super Renamer: "Type names" returned no results for loadable families (Doors, Windows, etc.) because `OfCategoryId + WhereElementIsElementType` can silently return empty in IronPython 3. Added a `OfClass(DB.FamilySymbol)` fallback with integer-based category ID comparison. Also replaced `cat.CategoryType in (...)` tuple checks with explicit `==` comparisons for IronPython 3 enum reliability.
- Super Renamer: added a third-stage fallback to "Type names" collection that scans `DB.Family` objects by category name string and collects types via `GetFamilySymbolIds()`. This is immune to `ElementId` comparison issues in IronPython 3 and ensures loadable families like Doors are always found.
- Super Renamer: fixed `_get_name()` to fall back to `SYMBOL_NAME_PARAM` / `ALL_MODEL_TYPE_NAME` built-in parameters when `element.Name` raises `AttributeError`. Root cause: `DB.Element.Name` has no getter (`CanRead=False`) for `FamilySymbol` in this IronPython 3 / Revit build -- the property is write-only. The setter (`element.Name = ...`) continues to work and is unchanged.

### Removed

- Rename Param Value (by Category) and Rename Param Value (by Selections) ribbon buttons removed -- Super Renamer now covers all their functionality plus type params, auto-load, and the full category list.
- Super Renamer: parameter list now loads automatically when the category changes in param mode -- the "Load Params" button has been removed.
- Import Key Schedule: added **Door Key Schedule** as a third target category alongside Area and Room. The `resolve_schedule_target` function now uses an indexed lookup table (`_TARGET_MAP`) instead of a hard-coded if/else, making future category additions a one-liner.

### Fixed

- Import Key Schedule: replaced non-ASCII em dash in a source comment that caused IronPython to fail importing the script.

## [2.1.0] - 2026-06-23

### Added

- Renumber Sheets: added **ISO 19650 mode** checkbox. When checked, the "Starting Number" field is replaced by a single **Pattern** field. Type the full sheet number with the segment to increment wrapped in brackets - e.g. `515T[200]D1`. The tool increments only that bracketed segment, leaving the surrounding characters fixed. Example: selecting 12 sheets and entering `515T[200]D1` renumbers them `515T200D1` to `515T211D1`.

### Changed

- Family Swapper: now works with all loadable family categories, not just Titleblocks. The family dropdowns are populated from every `FamilySymbol` in the document, and auto-detection from selection works for any `FamilyInstance`.

### Fixed

- Super Renamer: replaced non-ASCII em dash characters in two UI strings that caused IronPython encoding errors.
- Renumber Sheets: added pre-flight check — target sheet numbers are validated against all unselected sheets before the transaction starts, preventing a mid-rename rollback when a new number conflicts with an existing sheet.
- Renumber Sheets: added no-op guard — if the pattern or starting number generates the same sheet numbers as the current selection, the tool now shows a clear error explaining what to enter instead of silently committing an invisible rename.
- Renumber Sheets: two-pass rename now uses an index-based `_tmp_{n}` temp name instead of `_renumber_tmp_<original>`, eliminating length-overflow risk on long sheet numbers.
- Renumber Sheets: regex in ISO 19650 parser was greedy and would pick the **last** bracket group when a pattern contained multiple (e.g. `[100]T[200]D1` would have incremented `[200]` incorrectly). Fixed with non-greedy match to always pick the first bracket group.
- Renumber Sheets: guard checking for the UI helper function was checking the wrong function name, so a stale DLL would crash with `AttributeError` instead of showing a friendly reload prompt.
- Renumber Sheets: `build_new_numbers` had a dead `reference_list` parameter that callers were passing, believing conflict-checking was happening. Removed the parameter; no conflict-checking was ever implemented.
- Renumber Sheets: `result.Iso19650Mode` access in `WWP_uiUtils.py` is now guarded with `getattr(..., False)` so a stale in-process DLL does not crash on tool launch.
- Renumber Sheets: `__main__` block now pre-loads the UI object before the try block so the error alert is available even if `load_uiutils()` itself is the failure source. Added telemetry tracking.
- Family Swapper: auto-detection from selection now only fires when all selected `FamilyInstance` elements share the same family — a mixed selection no longer pre-populates the wrong source family.
- Family Swapper: `_run()` now reuses the type-ID map built at dialog open instead of issuing a redundant `FilteredElementCollector` query on every Run click.
- Import Key Schedule: `_elem_id_int` fallback branch used `.Value` (Revit 2024+ attribute) instead of `.IntegerValue`, causing `AttributeError` on Revit 2023 and earlier.
- Import Key Schedule: replaced the schedule-probe transaction in `_get_schedulable_parameter_options` with a transaction-free `doc.ParameterBindings` lookup. The old probe created a temporary `ViewSchedule` inside a `DB.Transaction` and rolled it back, which corrupted Revit's undo stack and made the import operation non-undoable.

## [2.0.1] - 2026-06-22

### Changed

- Parking Count in Room: now also collects areas (OST_Areas) from the active view, so the tool works in area plan views as well as floor plan views. The dialog list and result report use "rooms/areas" terminology.
- Parking Count in Room: target parameter dropdown is now restricted to text (string) parameters only.
- Parking Count in Room: added a **Parking families to include** multi-select list (pre-populated from the view). A text box and **+ Add** button let users include families not currently visible. Only selected families contribute to the count.
- Parking Count in Room: replaced the always-on type-source combo with an **Include parking type breakdowns** checkbox. When unchecked, the tool writes just the total count. When checked, a Type parameter combo appears and the written value becomes a multi-line breakdown ("TypeA: 3\nTypeB: 2\n\nTotal: 5").

## [2.0.1] - 2026-06-26

### Fixed
- Update WWPTools: structural-change updates no longer attempt a pyRevit hot-reload, which caused a `'The name already exists: MatchProperty'` ribbon error when updating from older versions. Users are now prompted to restart Revit instead, which is the only reliable way to rebuild the ribbon cleanly.

## [2.0.1] - 2026-06-18

### Changed
- Import Key Schedule, Export to Excel/CSV: file paths saved to config now store `%USERPROFILE%` in place of `C:\Users\<username>` so settings are portable across user accounts. Paths are expanded back via `%USERPROFILE%` when the dialog reopens.
- Export to Excel/CSV: if `! P_STATS_Export_Text` is absent from Project Information, the tool now prompts the user to select any existing writable text parameter as the saved-sets store instead. The chosen parameter is remembered per project. Previously, the tool silently returned empty sets on read and attempted to auto-create the parameter on write.
- Export2Ex Beta: moved the Delete button from the bottom toolbar into each row, next to the Edit button. Each set can now be deleted directly without checking a checkbox first.
- Export to Excel (both tools): added **Save Settings...** and **Load Settings...** buttons to the batch/manage dialog. Save Settings exports all saved sets to a portable `.settings` JSON file; Load Settings imports them and merges into the project, allowing settings to be shared across projects or team members.
- Export2Ex Beta: redesigned main dialog to match the by-schedule workflow — removed the Saved Set dropdown and Excel file path from the top; added editable **Set name** field; **Batch Export** button replaced by **Save** which saves the current configuration under the given name. File paths are managed per-set in the Manage Sets dialog.
- Export to Excel (by schedule): completely redesigned as a single **Batch Export** primary interface. Each row shows an editable Set Name, a **Schedule** dropdown with live contains-filtering (type any substring to narrow), a read-only **Sheet Name** preview, and File Path. Inline **Export** and **Delete** per row replace the old Edit button. Footer: **Settings** (export mode, header toggles, CSV options, use-category-as-sheet-name defaults to on), **Add Set**, **Save** (persists all row edits to Project Information), **Export Selected**, **Cancel**. The separate configuration dialog is no longer shown on tool launch.

### Added
- Update WWPTools: converted the updater into a pulldown with three commands: **Update WWPTools**, **Generate Updater Manually**, and **Force Updater**.
- Update WWPTools: added **Generate Updater Manually** to create and launch the external updater batch file regardless of the detected update condition.
- Update WWPTools: added **Force Updater** to prepare a fresh git clone with pyRevit's bundled git support, launch a visible batch window, wait for Revit to close, delete the installed extension folder, move the prepared clone into place, verify `.git`, and clean temporary marker files.
- GitBook: added Update WWPTools subpages for the normal updater, manual updater generation, and force updater workflows.

### Changed
- Update WWPTools: normal updates now classify incoming changes before updating. Existing non-DLL file edits update without reloading pyRevit; file/folder structure changes update then reload pyRevit; DLL changes generate and launch a batch updater that waits for all Revit processes to close.
- Update WWPTools: no-Git machines now use a PowerShell GitHub ZIP fallback for standard installs and updates instead of relying on `pyrevit.exe extend`.
- Force Updater: the batch window now appears before clone preparation, shows preparation status while pyRevit downloads the fresh clone, and only asks users to close Revit after the temp clone is ready.

### Fixed
- Update WWPTools: repair flow now detects when `WWP_Revit_WWPTools.extension` exists as a file instead of a folder and replaces it cleanly.
- Force Updater: no longer requires Git for Windows; the real git clone is created by pyRevit before Revit closes, then moved into place by the batch.

## [2.0.1] - 2026-06-17

### Changed
- Print Sheets: Naming Format editor replaced drag-and-drop tag panel with categorised clickable tag buttons (Sheet, Revision, Project, System) and dropdown pickers for Sheet, TitleBlock, Project Info, and Global parameters — dropdowns are populated from the open model at dialog load time and are also editable for custom parameter names.
- Print Sheets: Naming Format editor now has a "Create" button (alongside Duplicate and Delete) to start a new blank format from scratch.

### Fixed
- Combined Print Set: PDF merge error now reports the actual WWPTools.IO failure reason alongside the fallback pypdf error instead of only showing "invalid syntax".

## [2.0.1] - 2026-06-15

### Added
- Export2Ex (by Category): categories and parameters from Revit linked files are now included — the category list and parameter sampling both scan all loaded linked documents in addition to the host model.
- Export2Ex (by Category): Units selector ("Project units" / "Internal (feet)") — defaults to project units so length/area values now export in the document's display unit instead of always imperial feet.
- Export2Ex (by Category): "Id -- always exported" shown as a permanent first entry in Selected Properties so it is always visible that the Id column will be present in the export.

### Changed
- Export2Ex (by Category): dialog layout redesigned — sheet name and file path moved to top, mode selector replaced with a "Category From Schedule" checkbox toggle (By Category is now the default), source list given its own full-width section, Available Parameters and Selected Properties are equal-width side-by-side panels, Move Up/Down buttons moved to the centre column alongside Add/Remove.
- Export2Ex (by Category): "Linked properties" panel renamed to "Selected Properties".
- Export2Ex (by Category): tool pushbutton folder renamed from `export2ex_beta` to `export2ex_byCategory`.

### Fixed
- Export2Ex (by Category): categories and schedules in the source list were displaying as "IronPython.NewTypes.System.Object_1$1" instead of their names.
- Export2Ex (by Category): read-only parameters no longer display in grey — label and tooltip still indicate read-only status.

## [2.0.1] - 2026-06-12

### Changed
- Sheet Scale Updater: Target Parameter dropdown now groups parameters by titleblock family name, with a bold family header and separator between groups — prevents parameters from a different titleblock family appearing as if they apply to all sheets.
- True North Updater: same grouping applied to the Target Parameter dropdown.

## [2.0.1] - 2026-06-11

### Changed
- All tools: removed version number from tooltip text in all bundle.yaml files.

### Fixed
- Renamer (param_value_renamer_common.py): replaced em dash with ASCII hyphen on line 453 to fix IronPython SyntaxError (non-ASCII character '\xe2').

## [2.0.1] - 2026-06-08

### Added
- Export to Excel (Beta): **Batch Export manager is now the primary screen** — launching Export2Ex Beta opens the set manager directly. Add, Edit, and Delete buttons let you create or modify sets without leaving the manager; clicking Add or Edit opens the full config form. Clicking Export Selected runs the batch export.
- Export to Excel (Beta): **Batch Export** button — select multiple saved sets and export them all in one click. A table shows **Set Name**, **Sheet Name** (editable inline), and **File Path** columns; each row has a **...** button to choose or change the output file. Path and sheet name changes are written back to the project so they persist for future runs. If the `! P_STATS_Export_Text` parameter does not exist it is created automatically.
- Export to Excel (Beta): **excel_path** is now saved as part of each named set, so each set remembers its destination file across sessions. Loading a saved set restores the file path into the Export dialog.
- Export to Excel (Classic): added **Use Category as Sheet Name** toggle — when enabled, each exported sheet is named after the schedule's Revit category pluralised (e.g. "Parkings", "Areas", "Walls") instead of the full schedule view name. Multiple schedules with the same category get `_1`, `_2` suffixes as normal.
- Export to Excel (Classic): **per-set destination path** — the Excel file path and CSV folder are now saved as part of each named set so each set always exports to its own file.
- Export to Excel (Classic): **Batch Export** button — select multiple saved sets and export them all in one click; each set uses its stored destination path. A summary reports how many succeeded and lists any skipped sets with the reason.
- Export to Excel (Classic): Batch Export now shows a table with **Schedule Name**, **Sheet Name**, and **File Path** columns. Each row has a **...** button to choose or change the output file path before exporting. Unchecking a row skips that set. Path changes are written back to the project saved sets so they persist for future runs. If the `! P_STATS_Export_Text` parameter does not exist on the project, it is created automatically.

### Changed
- Export to Excel (Classic): **Excel file path** moved above the Search schedules field (below Saved set) so it is always visible without scrolling.
- Export to Excel (Classic): **CSV output options** (CSV folder, delimiter, text qualifier, quote all fields) are now hidden when Excel mode is selected and only appear when CSV mode is active.

### Added
- New compiled I/O layer (`WWPTools.IO`): a Revit-free C# DLL bundling Excel (ClosedXML) and PDF (PdfSharp) so file I/O runs in engine-agnostic compiled code instead of CPython-only Python libraries. All dependencies are ILRepack-merged into a single self-contained DLL per target framework (`lib/WWPTools.IO.net48.dll`, `lib/WWPTools.IO.net8.0-windows.dll`).

### Changed
- Excel **writing** now routes through `WWPTools.IO.ExcelService` (ClosedXML) for all four exporters — **Export to Excel (Classic)**, **Export to Excel (Beta)**, **Export Type Layers**, and **Copy Color Scheme** export. Appending into an existing workbook now preserves the styling/formulas/charts of sheets the tool doesn't touch (the native writer kept only values). If the DLL is unavailable the tools fall back to the native writer, so writes never fail hard. Excel **reading** continues to use the native in-process reader.
- Super Printer (Combine PDFs) now merges via `WWPTools.IO.PdfService` (PdfSharp), falling back to the bundled `pypdf` if the DLL is unavailable.

### Fixed
- Import Color Scheme from Excel: importing into a scheme that colors by a different parameter type no longer crashes with a raw Revit traceback (`...whose storage type is different with the scheme. Parameter name: colorFillData`). The merge now detects the target scheme's value type, skips new entries whose type doesn't match, applies the compatible ones, and folds a plain-language warning listing what was skipped into the final success message. If `SetEntries` still rejects the batch (e.g. an empty target scheme), the tool shows the same warning instead of terminating. The rejection is detected by the API parameter name (`colorFillData`), not the English message text, so it also works on non-English Revit installs, and both the initial and post-regenerate `SetEntries` calls are guarded.
- Import Color Scheme from Excel: fixed a crash that closed Revit every time the tool ran. `_elem_id_int()` called itself unconditionally, causing infinite recursion / `StackOverflowException` (which .NET cannot catch, so it terminated the process). It now reads `ElementId.Value` (Revit 2024+) / `IntegerValue` (2023-) directly.
- Excel tools on pyRevit 6.4: fixed "openpyxl is not available / module is not callable". openpyxl is a CPython-only library and cannot import under IronPython, but pyRevit 6.4's CPython engine breaks the WPF event delegates these dialogs rely on — so neither engine worked. Added `lib/WWP_xlsx.py`, a native .NET (`System.IO.Compression` + `System.Xml.Linq`) `.xlsx` reader/writer that mimics the slice of the openpyxl API these scripts use, so they stay on IronPython (delegates work) with no openpyxl dependency. Affected tools now using it: **Import Key Schedule from Excel**, **Import Type Layers**, **Export Type Layers**, **Export to Excel (Classic)**, **Export to Excel (Beta)**, and **Copy Color Scheme** (import/export, including reading cell fill colours). Notes: date cells are read as their raw Excel serial number rather than a formatted date; appending into an existing workbook preserves the cell values of pre-existing sheets but not their original styling/formulas/charts; `.xlsm` macros are not preserved on save.
- Mass Stats CSV export was already IronPython-safe (uses the `csv` module, not openpyxl) and is unaffected.
- Export to Excel (Classic + Beta): saved sets were silently failing to persist in the project. Parameter creation and value write are now combined in a single transaction with `doc.Regenerate()` between binding creation and the `LookupParameter` call, fixing the stale-reference issue on first use. Classic now also shows an alert if writing fails instead of silently discarding the save.
- Export to Excel (Classic + Beta): fixed namespace collision — Classic was misreading Beta's saved sets (and vice versa) because the legacy-data fallback in `read_saved_sets` and `_looks_like_legacy_saved_sets` only checked for its own namespace key. Both functions now check all known namespaces (`export2ex`, `export2ex_beta`, `mass_stats`) before falling back to legacy mode.

### Fixed
- Add Line Type: replaced wildcard `from Autodesk.Revit.DB import *` with explicit symbol imports to reduce first-click load overhead.

### Added
- Renamed "Duplicate Views For Level" to **Propagate Views to Levels**. Fixed P-level view naming (views now carry the full target level name, e.g. "LEVEL P1", instead of losing the "P" prefix). Fixed ceiling plan / floor plan collision: tracking is now per view type so both can be created with the same name.
- Level Setup: existing levels are now renamed to the canonical FLOOR XX format when their elevation is updated (e.g. "Level 1" becomes "FLOOR 01"). Avoids name collisions by excluding the level's own current name from the uniqueness check.
- Level Setup: diagram now draws L1 (or L00) nearest the ground line and the highest floor at the top, matching a real building section orientation.
- Level Setup: added "Numbering Convention" toggle — choose L01/L02 (North America) or L00/L01 (UK/Europe). The dialog labels, live section diagram, and Revit level names all update accordingly. Levels are created as FLOOR 00, FLOOR 01... in UK mode.
- Level Setup: redesigned input dialog with two-column layout — structured form (Above Ground / Underground sections with inline label + value rows) alongside a live building cross-section diagram that updates in real time as you type. Floor slabs, level labels (L1, L2… / P1, P2…), and height annotations are drawn dynamically on a sky/earth canvas.
- Update WWPTools: split update strategy for DLL-containing updates. Python/config files that changed are now updated in-place immediately (no restart needed), and pyRevit reload is offered. DLL files get a one-click `UpdateWWPTools.bat` written to `%LOCALAPPDATA%\WWPTools\PendingUpdates\` — Explorer opens to it automatically. Close Revit, double-click the script. No git CLI? The bat falls back to PowerShell zip download from GitHub (DLLs only; run Update once more after to sync the git record).
- Mass Stats: status bar now shows "Calculating..." immediately when a refresh is triggered (Refresh button or auto-refresh debounce), giving instant feedback on slow or linked models.
- Mass Stats: Delete Set now requires confirmation before removing a saved set (the action is non-undoable).
- Mass Stats: "Write units to mass" button is now disabled until a write target parameter is selected; enables automatically when a parameter is chosen.
- Mass Stats: "Export CSV..." button exports one row per mass floor to a CSV file. Columns include all string parameters found on the mass floor and its parent mass (in priority order: Mass ID, Building ID, Block, Program, Site, then alphabetical), plus fixed columns for Level, Design Option, area, and — for rows that match the current filter — GIA ratio, NIA ratio, GIA sqm, NIA sqm, estimated total units, and unit-type breakdown (Studios through 4-Bed+). Opens a Save dialog; the file is UTF-8 with BOM for Excel compatibility. Works in both By Filters and By Selection modes.

### Fixed
- Mass Stats: all hardcoded hex colour values in XAML replaced with theme token references (`SurfaceBrush`, `AccentSoftBrush`, `DangerBrush`, `DangerSoftBrush`, `TextBrush`) — error panel and accent card were visually mismatched against the rest of the UI.
- Mass Stats: `FontWeight="Medium"` on the Button base style in `FlatTheme.xaml` changed to `SemiBold`; `Segoe UI Variable` has no weight-500 face so all buttons were silently rendering at Normal weight.
- Mass Stats: filter row labels ("Parameter", "Operator", "Value") now use the `MutedBrush` theme token instead of `Brushes.Gray` (#808080 vs the theme's #5F7185).
- Mass Stats: combo boxes (`CboSavedSet`, `CboMixParam`, `CboWriteParam`) no longer override `Height="28"` — they now use the theme's `MinHeight="34"` and render at the same height as adjacent buttons.
- Mass Stats: removed the unused `CardValueLarge` XAML style (dead code, was never applied).
- Mass Stats (By Filters): removed UTF-8 BOM, box-drawing characters, and Unicode arrow from script — IronPython defaulted to ASCII and raised `SyntaxError: Non-ASCII character` at parse time, preventing the tool from loading.
- Mass Stats (By Filters, By Selection): DLL loader now selects `net8.0-windows.dll` for Revit 2025+ and `net48.dll` for Revit 2024 and earlier, matching the pattern used by other WPF tools; previously `net48.dll` was always loaded first regardless of Revit version, causing type-resolution errors on Revit 2025+. Switched to `clr.AddReferenceToFileAndPath` where available.
- Mass Stats: `_isExecuting = true` is now set before `_refreshQueued = false` in the external event handler, closing a race window where a `DocumentChanged` event could permanently block all future dashboard refreshes.
- Mass Stats: dashboard now auto-refreshes after Save Settings, Save Set, and Delete Set operations; previously the display stayed stale until the user manually clicked Refresh.
- Mass Stats: `GetElementIdValue` now returns `long` instead of `int`, preventing silent overflow for large Revit 2025+ element IDs (> 2 billion) that corrupted selection-scope filtering in By Selection mode.
- Mass Stats: `GetDesignOptionSetName` now constructs `ElementId` from `(long)param.AsInteger()` to be compatible with the Revit 2025+ API where the `ElementId(int)` constructor was removed.
- Mass Stats: `GetFloorAreaFt2` generic fallback now requires an exact "Floor Area" name match instead of a substring `Contains("area")` check, preventing false matches against custom parameters like "Fill Area" or "Surface Area" that would silently produce wrong GEA totals.
- Mass Stats: removed unreachable `switch(p.StorageType)` block in `WriteUnitCountsToMass` — the preceding guard already ensures `StorageType == Integer`, making the switch and its dead `default` branch unnecessary.

### Changed
- Bundled `openpyxl 3.0.10` and `et_xmlfile 1.1.0` into `lib/` (pure-Python, no C extensions). The 5 previously CPython-only scripts (`ExportTypeLayers`, `ImportTypeLayers`, `Export2ex`, `Export2exBeta`, `Key Schedule`) have had their `#! python3` engine headers removed and now run under IronPython using the bundled library. No Python installation required on team machines — the packages are distributed automatically via the extension's git update.
- Family Swapper: source family is now auto-detected from the current Revit selection (falls back to first available family); target family is a sorted dropdown of all titleblock families in the model; type mapping rows now use `DataGridComboBoxColumn` dropdowns populated from each family's types, with auto-matching by name on family change; parameter mapping rows now use editable `DataGridComboBoxColumn` dropdowns populated by the Discover button.
- Rolled back 10 scripts from `#! python3` (CPython) to IronPython by removing the engine header — pyRevit 6.4 does not support `forms` under CPython. Scripts that remain on CPython are those with a hard `openpyxl` dependency: `ImportTypeLayers`, `ExportTypeLayers`, `Export2ex`, `Export2exBeta`, `Key Schedule`. Rolled-back scripts: `Add Line Type`, `SetupLevel`, `DuplicateViewsForLevel`, `CreateViewsFromLevel`, `WipeDataExchange`, `massstats-byfilters`, `massstats-byselection`, `Push Room Numbers to Door Mark`, `ukcontextbuilder`, `webcontextbuilder`.
- UI theme overhaul (`FlatTheme.xaml`): rounded corners on TextBox, ListBox, ComboBox, and ListBox; blue focus ring on text inputs; pressed state on buttons; modernised CheckBox and RadioButton with custom templates; GroupBox chrome replaced with clean card-style header; slim 6 px scrollbars with pill-shaped thumbs; dark tooltip style; DataGrid row hover highlight; ListViewItem hover/selection added. Font upgraded to `Segoe UI Variable` (Windows 11) with `Segoe UI` fallback.
- `MatchPropertyWindow.xaml`: removed `FontFamily="Arial Narrow"` override so the window inherits the theme font.

### Fixed
- Family Swapper: added "Set target" button next to the target family dropdown; clicking it discovers parameters without requiring a placed instance of the target family -- both instance and type parameters are read from `doc.ParameterBindings` filtered to the source family's actual category (not hardcoded to `OST_TitleBlocks`), so the tool is not limited to titleblocks. All element collectors in `_run`, `_instances_of_family`, and `_discover_click` now use `OfCategoryId(_src_cat_id)` instead of `OfCategory(OST_TitleBlocks)`. Target type ID resolution now filters by target family name in addition to type name, preventing accidental matches when source and target families share type names. Type parameter writes during run are now skipped with a post-run notice (writing type params would silently affect all instances sharing that type, not just the current sheet). Previously the target parameter column was incorrectly showing source family parameters because `Discover` matched target types by name across all families rather than by family. Parameter discovery and run now also include type parameters in addition to instance parameters; the run step falls back to writing type parameters when the target instance parameter is not found.
- `FamilySwapper-script.py`: replaced three em dash characters (`\xe2\x80\x94`) with plain hyphens — IronPython defaults to ASCII and raised `SyntaxError: Non-ASCII character '\xe2'` on load.
- `FamilySwapper-script.py`: added `clr.AddReference('PresentationFramework')` before the `System.Windows.Controls` import — IronPython raises `ImportError: No module named Controls` without it.
- `ukcontextbuilder-script.py`, `webcontextbuilder-script.py`: added `clr.AddReference` calls for `System`, `System.Xml`, `PresentationFramework`, `PresentationCore`, and `WindowsBase` before the `from System.*` imports — IronPython does not auto-load these assemblies unlike CPython/pythonnet.
- `ExportTypeLayers`, `ImportTypeLayers`, `Export2ex`, `Export2exBeta`, `Key Schedule`: added `clr.AddReference('System.Xml')` (and `import clr` where missing) immediately before each lazy `import openpyxl` call. IronPython's `xml.etree.ElementTree` internally uses `System.Xml.XmlReader`; without an explicit reference the runtime raised `ImportError: Cannot import name XmlReader` on first use.
- `Export2ex`, `Export2exBeta`, `Key Schedule`: moved `clr.AddReference('System.Xml')` to the top of each script (right after `import clr`) so it runs before the WPF dialog loader calls `from System.Xml import XmlReader`. Previously the reference was only added inside `export_to_excel` / `read_workbook`, which runs after the dialog — causing `ImportError: Cannot import name XmlReader` on every launch.
- Patched all 19 f-strings, 1 underscore numeric literal (`1_000_000` in `utils/datetime.py`), and 1 module/class name collision (`xml/functions.py`: changed `from et_xmlfile import xmlfile` to `from et_xmlfile.xmlfile import xmlfile` to avoid IronPython returning the `et_xmlfile.xmlfile` module object instead of the `xmlfile` class — caused `TypeError: module is not callable`) in bundled `openpyxl 3.0.10` (`cell/cell.py`, `cell/_writer.py`, `drawing/spreadsheet_drawing.py`, `formula/tokenizer.py`, `styles/numbers.py`, `utils/cell.py`, `workbook/_writer.py`, `worksheet/worksheet.py`, `worksheet/_reader.py`, `worksheet/_read_only.py`, `worksheet/_writer.py`). IronPython 3.4 (pyRevit 6.4) does not support f-strings (Python 3.6 syntax); the syntax error was masking as "openpyxl is not available. invalid syntax".
- `webcontextbuilder-script.py`, `ukcontextbuilder-script.py`: added `_el_name(el)` helper that wraps `.Name` in a `try/except` returning `''` on failure. All `floor_type.Name.lower()` and `topo_type.Name.lower()` sort keys and inline reads now go through this helper. IronPython 3 converts internal C# `NullReferenceException` in the `.Name` getter to `AttributeError: Name`, which previously crashed the floor/topo type pickers.
- `Export2exBeta-script.py`: added `clr.AddReference('PresentationCore')` and `clr.AddReference('PresentationFramework')` before the `from System.Windows.Media` and `from System.Windows.Controls` imports — IronPython raises `ImportError: No module named Media` without an explicit reference to `PresentationCore`.

## [2.0.0] - 2026-05-22

### Added
- Sheet Scale Updater and True North Updater: added optional "Also set a visibility parameter on the titleblock" toggle. When enabled, a Yes/No instance parameter selected from a ComboBox is set to True (1) on each updated titleblock — useful for automatically making a scale bar or north arrow visible on processed sheets. The selected parameter is persisted per-project in tool settings. Sheet Scale Updater also gains "Also hide on sheets with no scaleable views" (sets the visibility param to No when a sheet has only legends/drafting views). True North Updater also gains "Also hide on sheets where the primary view is an elevation or section" (sets the visibility param to No and reports to a separate Hidden list).
- `tools/sync_bundle_versions.py`: dev utility that reads `WWPTools.version.json` and updates every `Version: X.X.X` occurrence in all `bundle.yaml` files under `WWPTools.extension/`. Runs automatically as a CI step in the publish workflow before rsync so the distribution repo always has the correct version in every tooltip.
- Family Swapper: new tool in Manage panel → Match Property pulldown. Swaps all instances of a source titleblock family to a target family in configurable batches; runtime WPF form lets user configure source family, type mappings, parameter remapping (with auto-discover from project), shift offsets, and batch size. Match Property moved into the same pulldown.
- Auto-update on Revit close: `startup.py` registers a `UIApplication.ApplicationClosing` event at pyRevit startup. Silently checks for updates in a background thread; if updates are available when the user closes Revit, a TaskDialog prompts to install. A detached batch watcher waits for Revit to fully exit, waits 15 seconds, then runs `git fetch / reset --hard / clean` and sends a toast notification on completion.
- Script usage logging (Python): rewrote `WWP_telemetry.py` to POST log entries to a Neon Postgres database via a Vercel API endpoint. Uses `urllib` only (no pip dependencies). Adds `track_use()` function; preserves `track_current_command()` and `track_app_init()`. Falls back to a local JSONL queue at `%APPDATA%\pyRevit\WWPTools\pending_script_logs.jsonl` when offline; queue flushes automatically on next successful connection.
- Script usage logging (C#): added `ScriptLogger.cs` to `WWPTools.WpfUI` (`WWP.Revit.Logging` namespace). Fetches Neon connection string from live Vercel config endpoint (`wwp-revit-wwp-tools-logger.vercel.app`) on first call; writes directly via Npgsql. Uses same offline queue path as Python side.
- `command-executed` pyRevit hook: automatically logs every WWPTools button click without modifying individual scripts.
- Error telemetry: tool crashes are automatically logged to Neon with the exception message and traceback (truncated to 1 000 chars) via a new command-failed pyRevit hook.
- Npgsql 8.0.5 NuGet package added to `WWPTools.WpfUI`.
- About dialog: telemetry opt-out toggle — checked by default; unchecking stops all usage reports. Preference saved to `%APPDATA%\pyRevit\WWPTools\user_prefs.json`.

### Fixed
- pyRevit 6.4 IronPython rollback: removed `#!python3` engine header from 18 scripts that do not use CPython-only libraries (`openpyxl`, etc.), reverting them to IronPython to resolve `forms` compatibility issues in pyRevit 6.4. `Key Schedule-script.py` retains the header as it depends on `openpyxl`.
- pyRevit 6.4 compatibility: removed all .NET delegate type wrappers (`RoutedEventHandler`, `SelectionChangedEventHandler`, `TextChangedEventHandler`, `MouseEventHandler`, `MouseButtonEventHandler`) from WPF event bindings and imports across 36 files — pythonnet 3.x crashes on any delegate type construction; direct `event += func` works on all runtimes — pythonnet 3.x (shipped with pyRevit 6.4) fails to initialise the `Delegates` type when this wrapper is used; direct assignment (`event += func`) works on IronPython, pythonnet 2.x, and pythonnet 3.x. Also fixed `FileNotFoundError` → `IOError` in Mass Stats scripts for IronPython compatibility, and moved `#! python3` engine header to line 1 so pyRevit 6.4 correctly routes those scripts to CPython.
- Telemetry: `app-init.py` now handles both the plain-list and `{"value": [...], "Count": N}` pyRevit telemetry file formats when processing previous-session records.
- Telemetry: fixed `UnicodeEncodeError` crashes in IronPython 2.7 — `open()` defaults to ASCII encoding, which can't encode emoji characters (✅/❌) generated by `_format_details_md`. Fixed in `app-init.py` (batch JSONL write), `WWP_telemetry._write_local_log`, `_queue`, and `_flush_pending` — all now use `io.open` with explicit UTF-8 encoding.
- Telemetry: `_fire` in `WWP_telemetry.py` now calls `_worker` synchronously instead of spawning a daemon thread — daemon threads started from inside a pyRevit script context are killed when the script finishes, so the HTTP POST to Neon never completed. Affects all `track_use`, `track_app_init`, and `track_failed_command` calls.
- Telemetry: batch processing in `app-init.py` now calls `_worker` synchronously instead of spawning daemon threads for each entry — same daemon thread lifecycle issue.
- Telemetry: fixed `_post` using `ensure_ascii=False` in `json.dumps` — IronPython 2.7's json module can raise `UnicodeEncodeError` when serialising dicts containing unicode emoji with the default `ensure_ascii=True`, causing all Neon POSTs to fail silently and entries to accumulate in the pending queue indefinitely.
- Telemetry: removed `journal-command-exec.py` hook — in pyRevit, `EXEC_PARAMS.command_name` inside a hook always returns the hook's own identifier, not the button that triggered it. Tool-use logging now relies entirely on `app-init.py` batch processing of pyRevit's previous-session telemetry files.
- `#! python3` engine directive moved to line 1 in 10 scripts (`Add Line Type`, `SetupLevel`, `CreateViewsFromLevel`, `DuplicateViewsForLevel`, `FireRatingAll`, `FireRatingSelected`, `FireRatingFRRViews`, `DoorFireRating`, `CombinedPrintSet`, `Wipe Data Exchange Geometry`) that had the directive missing or on the wrong line, preventing pyRevit 6.4 from routing them to CPython.
- True North Updater: angle was written as the clockwise value (e.g. 342.8°) instead of the correct counter-clockwise value (e.g. 17.2°). Fixed sign convention in `_get_true_north_angle_for_view`.
- Distribution repo now receives a matching `V{version}` git tag on every publish, so pyRevit's extension manager displays the correct version instead of the previous tag.

### Changed
- Update WWPTools: button now available without an open Revit document (`zero-doc` context).
- Update WWPTools: removed standby updater (detached batch watcher that ran after Revit exited). If an update includes locked DLLs, the tool now simply prompts to close Revit and try again.
- `DeleteSheetSet`: fixed parameter list showing unrelated view parameters.
- Sheets-Views Manager pulldown: reordered by function group.
- Export2Ex and Beta: renamed Project Info parameter to `! P_STATS_Export_Text`.
- Export2Ex: added Project Info settings sync and named save sets.
- Script usage logging (Python): rewired to call C# ScriptLogger directly via CLR -- no separate Vercel POST endpoint required.
- Script usage logging (Python): telemetry payload now populates all Neon columns — `session_id`, `event_type`, `pyrevit_version`, `wwptools_version`, `document_name`, and `details`; `details` is a markdown-formatted human-readable report of each script run.

## [1.5.0] - 2026-05-04

### Changed
- Release flow: this is the final transition MSI release for existing installer users.
- Installation guidance: WWPTools is switching to non-installer distribution. New installs and future updates should use pyRevit Extension Manager with the Git repository URL: `https://github.com/WWP-Architects-Planners/WWP_Revit_WWPTools`.
- Installer text: updated install descriptions and license messaging to point users to the pyRevit Git extension workflow.

### Fixed
- Installer bootstrap: supports the current extension-rooted distribution repository layout when downloading from GitHub.
- Startup version check: reads the local version file correctly from Git-cloned pyRevit extension folders.

## [1.3.3] - 2026-04-23

### Changed
- Update WWPTools: switched the updater to track the `main` branch now that the old `pyrevit` branch is gone
- Release documentation: aligned the publish secret name with the working GitHub Actions workflow (`COMPANY_WRITE_TOKEN`)

### Fixed
- Export2Ex Beta: the category exporter can now resolve a category from a selected schedule or let users pick a category directly, then export actual category elements with selected type/instance/shared/project parameters to Excel

## [1.2.8] - 2026-04-06

### Added
- Renamer: grouped the renaming commands under a new `Super Renamer` pulldown in `WWPTools.extension`

### Changed
- Super Renamer: split the tools into `Super Renamer(by Category)` and `Super Renamer(by Selections)`
- Super Renamer: both commands now use the same shared renaming workflow and preview/apply dialog

### Fixed
- Super Renamer(by Category): corrected the moved tool's WPF theme loading path after the pulldown reorganization
- Super Renamer: removed the `pyrevit.revit` dependency from the renamer launch path to avoid CPython startup failures
- Super Renamer: restored the Renamer pulldown icon so the ribbon logo displays again

## [1.2.9] - 2026-04-08

### Changed
- Release flow: documented GitHub URL / pyRevit Extension Manager distribution as the primary install and update path instead of MSI installers
- DirectShape To Mass: added an output-category picker so conversions can create either `Mass` or `Generic Model` families, while keeping `Mass` as the recommended option

### Fixed
- DirectShape To Mass: automatic discovery now falls back to any `DirectShape` in the `Mass` or `Generic Models` categories when no tagged Web Context / Building Importer shapes are found
- DirectShape To Mass: automatic discovery now also recognizes Autodesk Data Exchange geometry when the source element carries an `Exchange Name` value

## [1.2.6] - 2026-03-25

## [1.2.7] - 2026-04-01

### Added
- Export2Ex: restored the local `Export2Ex` pulldown layout with separate `Classic` and `Beta` exporters

### Changed
- Cleanup: renamed `Round Angles` to `Fix Angles` for a clearer tool label

### Fixed
- Export2Ex Beta and Classic: hardened Excel save dialog startup so invalid remembered paths do not crash the tool window, with safer fallback behavior for pyRevit 5.3.1 and newer environments
- Export2Ex: preserved the locally updated exporter workflow and beta entry during branch sync/recovery

### Added
- pyRevit compatibility: added a shared runtime compatibility layer for pyRevit 5.3.1 and 6.1 file, config, and HTTP handling

### Changed
- Installer and update checker: switched GitHub release and zip-download URLs to the current `WWP-Architects-Planners/WWP_Revit_WWPTools` repository
- pyRevit compatibility: removed unnecessary `python3` engine headers from version-neutral tools so they can run under older pyRevit installs without forcing CPython

### Fixed
- Combined Print Set: fail fast when a PDF printer leaves a hidden save dialog, never creates output, or stalls at `0 KB`, instead of hanging Revit for minutes per sheet
- Web Context Builder and UK Context Builder: replaced Python-3-only urllib imports with cross-runtime compatibility helpers
- Local File Cleaner, Create Template, and Fix Floor Heights: replaced Python-3-only config/file handling with cross-runtime compatibility helpers
- Export2Ex, Import Key Schedule, and Type Layers: removed postponed-annotation syntax that made the CPython-backed workflows more brittle across pyRevit versions

## [1.2.5] - 2026-03-19

### Added
- Views Sheet Manager: new `Lay Views on Sheet` tool with a CPython/XAML window, titleblock picker, draggable sheet preview, and automatic viewport array layout
- Views Sheet Manager: searchable in-window view selector for the `Lay Views on Sheet` tool with filter-based batch selection

### Fixed
- Import Key Schedule: Excel-to-parameter mapping selections now persist and reload correctly per target type and header signature

## [1.2.4] - 2026-03-19

### Added
- Import Key Schedule: converted the tool into a pulldown with `Import from Excel` and a new `Map by Name` action
- Import Key Schedule: new `Map by Name` tool for existing Rooms and Areas that matches host `Name` to key schedule `Name` and assigns the host key parameter automatically

### Changed
- Import Key Schedule: duplicate key schedule name matches now prefer the `Program = Residential` version by default when multiple rows share the same `Name`
- Manual Revisions: moved to a CPython/XAML workflow with selectable target titleblock swapping and multi-sheet processing
- Manual Revisions: added automatic multiline wrapping, current-sheet-first selection, optional single-column ignore behavior, and resizable split-pane UI

### Fixed
- Manual Revisions: removed per-sheet titleblock lookups from dialog startup to reduce load time
- Manual Revisions: improved wrapped text layout and overflow handling for left/right revision columns

## [1.2.3] - 2026-03-17

### Added
- Web Context Builder: new pyRevit/XAML context import tool with embedded Leaflet/OpenStreetMap map, click-to-set location, cached web data, layer toggles, and square-radius extent import
- UK Context Builder: new UK-only pyRevit/XAML context import tool using Environment Agency terrain services and DSM-minus-DTM fallback heights for buildings missing OSM height data
- Web Context Builder: optional HRDEM terrain import with Toposolid generation, dense-area sampling control, terrain-aware building placement, and Toposolid subdivisions for roads, tracks, parcels, parks, and water
- DirectShape To Mass: new conversion tool for turning imported DirectShape buildings into conceptual mass families
- Copy Parameter: split into `Copy Parameter From Selected` and `Copy Parameter By Category` tools under a new pulldown

### Changed
- Building Importer: replaced the old Dynamo-based workflow with a Python OSM importer and archived the legacy Dynamo graph
- Web Context Builder: buildings now import as DirectShape in the Mass category by default for faster runs and simpler visibility control
- Web Context Builder: roads, tracks, parcels, parks, and water now create or repair dedicated `WWP CONTEXT - ...` floor types with fixed 10 mm thickness and assigned materials on the flat-floor workflow
- Web Context Builder: context floor types are repaired on each run so existing legacy `WWP` floor types are updated to the current naming, thickness, and material rules

### Fixed
- Building Importer: fixed the material picker cancel path so it no longer throws `ElementId.op_Equality` errors
- Web Context Builder: fixed flat-floor type duplication/materialization so duplicated floor types no longer silently fall back to the original base type
- Web Context Builder: restored visible DirectShape edges by removing same-color line overrides on imported buildings
- Area Plan Duplicator: fixed area boundary recreation when copying between area schemes and report actual created/failed boundary counts

## [1.2.2] - 2026-03-16
- Flat UI refresh across WPF-based tool dialogs, including new XAML-backed dialogs where needed
- Export2Ex: moved to a flat XAML dialog, fixed dialog loading errors, and improved sizing with a resizable splitter layout
- Export2Ex: converted option checkboxes to toggle-style controls for a more consistent export form
- Sheet Scale Updater: added an `Ignore Drafting Views` toggle and warnings for sheets that contain only drafting views
- Import Area Key Schedule: improved automatic column mapping with direct contains matching and stronger fuzzy matching for parameter names like `*WWP_Stats_GCA`

## [1.2.1] - 2026-03-11
- Sheet Scale Updater: merged sheet selection and target parameter selection into one dialog
- Sheet Scale Updater: target parameter list now filters to non-Yes/No parameters containing `Scale`
- Sheet Scale Updater: current sheet is surfaced first and labeled in the picker
- Sheet Scale Updater: simplified reporting to show changed sheets and failed/skipped sheets only
- Area Plan Duplicator: fixed level list scrolling and added footer logo
- Schedule2Excel/CSV: added footer logo to the export dialog

## [1.2.0] - 2026-03-06
- Installer refresh release so users on installer `1.1.9` can reinstall and pull the latest script set from `main`
- Schedule2Excel/CSV: fixed Excel save path handling so `.xlsm` stays `.xlsm` (no forced `.xlsx` append)
- Schedule2Excel/CSV: Excel picker now supports both `.xlsx` and `.xlsm`
- Schedule2Excel/CSV: preserve VBA when exporting into existing `.xlsm` files
- Schedule2Excel/CSV: hardened CPython compatibility for pyRevit 6.1 by removing `pyrevit.revit` dependency path and adding safe config fallback
- Copy Color Scheme: added overwrite-vs-create target scope option and fixed overwrite persistence for `In Use` entries
- Copy Color Scheme: simplified logging to show only actionable copy/finalize results
- Room to Area Boundary: added multi-area-plan processing and consolidated reporting

## [1.1.8] - 2026-02-17
- Sheet Scale Updater: ignore legend viewports when calculating sheet scale
- Schedule2Excel/CSV: updates and fixes
- Fixed help/documentation links across tools

## [1.1.7] - 2026-02-11
- Sheet Scale Updater: ignore legend viewports when calculating sheet scale
- Schedule2Excel/CSV: updates and fixes
- Fixed help/documentation links across tools

## [1.1.6] - 2026-02-06

### Added
- Update checker on startup with GitHub latest-release comparison
- Local version file `WWPTools.extension/lib/WWPTools.version.json`
- About button icon and version tooltip
- Update WWPTools button (fetch/pull GitHub updates with user confirmation)
- Windows 10-style toggle styling in WPF dialogs

### Changed
- Sheet Scale Updater: sorted sheet list, selectable sheets, target parameter picker (instance + type), and performance tuning
- Copy Color Scheme: source list shows category/area scheme names; target selection is category-only with single overwrite toggle
- App-init toast now reports “outdated” vs “latest” on load

### Fixed
- CPython output flushing errors in Sheet Scale Updater report
- Cancel/exit handling to avoid CPython `SystemExit` errors
- IronPython update check import error (urllib fallback)
- Update button fallback when Git is missing (opens releases page)

## [1.1.3] - 2026-01-09

### Fixed
- Installer License Agreement dialog UI errors (first active control and tab order)
- MSI build step now patches dialog metadata after banner removal

## [1.1.5] - 2026-01-28

### Added
- Separate installers for WWPTools scripts and Dynamo packages
- Dynamo packages installer (v1.0.0) for package-only deployments

### Fixed
- Installer uninstall now removes installed extension/packages via uninstall actions

## [1.1.4] - 2026-01-16

### Added
- CPython versions of the Sheet Manager tools (delete sheets, delete sheet views, duplicate sheets)
- Sheet Duplicator UI with duplicate options, prefix/suffix, and view duplication controls
- Replace View Name CPython tool with find/replace/prefix/suffix inputs
- Delete Unused Views CPython tool with confirmation prompt

### Changed
- Renumber Sheets now uses a single dialog for selection and starting number
- Sheet Manager Dynamo graphs moved into archive folders

### Fixed
- UI helper now handles null/non-iterable inputs for sheet renumber selection

## [1.1.2] - 2026-01-09

### Changed
- Updated Mass Context tool bundles for Random Plants, Mass ID Tool, Detail Line CAD, and Rename Materials
- Updated Randomtree and CADLine scripts
- Updated `WWPTools.tab/bundle.yaml`

## [1.1.1] - 2026-01-08

### Changed
- Multiple Schedules Exporter completely rewritten for CPython with WinForms UI
- Excel export now uses openpyxl via CSV pipeline; CSV export unchanged
- Export tool now remembers last schedule selection, last Excel file location, and last CSV folder
- Schedule list excludes legend views
- Sync task excludes archive folders
- Refactored Export2Ex tool from pulldown menu to single pushbutton
- Consolidated multiple schedule export tools into unified interface
- Improved user experience with streamlined export workflow

### Removed
- Deprecated separate SingleSchedule and MultipleSchedules buttons
- Removed archive Dynamo scripts for schedule export

## [1.1.0] - 2026-01-07

### Added
- Reorganized Add Project Parameter tool into a pulldown menu with multiple options
  - Create Template functionality
  - Import from Excel functionality

### Changed
- Converted CopyParameter tool from Dynamo to Python script for better performance
- Refactored project structure for better organization
- Updated various tool scripts and configurations
- Updated extension hooks and library utilities
- Enhanced bundle configurations across multiple tools
- Improved line ending consistency across files

### Removed
- Deprecated package copy tools (copyallpackages pulldown)

### Archived
- Old implementations moved to archive folders for reference

## [1.0.0] - 2026-01-06

### Added
- Initial release of WWPTools
- Published with package for WW+P users
- Useful tools and shortcuts for better productivity
- Mass Context tools
- Project Management tools
- Project Setup tools
- Revit Cleanup tools
- Views Sheet Manager tools

[1.1.4]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.4
[1.1.5]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.5
[1.1.8]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.8
[1.1.7]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.7
[1.1.6]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.6
[1.1.3]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.3
[1.1.2]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.2
[1.1.1]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.1
[1.1.0]: https://github.com/jason-svn/WWPTools/releases/tag/V1.1.0
[1.0.0]: https://github.com/jason-svn/WWPTools/releases/tag/V1.0.0
[1.2.0]: https://github.com/jason-svn/WWPTools/releases/tag/V1.2.0
[1.2.4]: https://github.com/jason-svn/WWPTools/releases/tag/V1.2.4
[1.5.0]: https://github.com/jason-svn/WWPTools/releases/tag/V1.5.0
[2.0.0]: https://github.com/jason-svn/WWPTools/releases/tag/V2.0.0
