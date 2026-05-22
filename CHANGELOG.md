# Changelog

All notable changes to WWPTools will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-05-22

### Added
- Auto-update on Revit close: `startup.py` registers a `UIApplication.ApplicationClosing` event at pyRevit startup. Silently checks for updates in a background thread; if updates are available when the user closes Revit, a TaskDialog prompts to install. A detached batch watcher waits for Revit to fully exit, waits 15 seconds, then runs `git fetch / reset --hard / clean` and sends a toast notification on completion.
- Script usage logging (Python): rewrote `WWP_telemetry.py` to POST log entries to a Neon Postgres database via a Vercel API endpoint. Uses `urllib` only (no pip dependencies). Adds `track_use()` function; preserves `track_current_command()` and `track_app_init()`. Falls back to a local JSONL queue at `%APPDATA%\pyRevit\WWPTools\pending_script_logs.jsonl` when offline; queue flushes automatically on next successful connection.
- Script usage logging (C#): added `ScriptLogger.cs` to `WWPTools.WpfUI` (`WWP.Revit.Logging` namespace). Fetches Neon connection string from live Vercel config endpoint (`wwp-revit-wwp-tools-logger.vercel.app`) on first call; writes directly via Npgsql. Uses same offline queue path as Python side.
- `command-executed` pyRevit hook: automatically logs every WWPTools button click without modifying individual scripts.
- Error telemetry: tool crashes are automatically logged to Neon with the exception message and traceback (truncated to 1 000 chars) via a new command-failed pyRevit hook.
- Npgsql 8.0.5 NuGet package added to `WWPTools.WpfUI`.
- About dialog: telemetry opt-out toggle — checked by default; unchecking stops all usage reports. Preference saved to `%APPDATA%\pyRevit\WWPTools\user_prefs.json`.

### Fixed
- pyRevit 6.4 compatibility: removed `RoutedEventHandler(func)` wrappers from all WPF event bindings across 23 files — pythonnet 3.x (shipped with pyRevit 6.4) fails to initialise the `Delegates` type when this wrapper is used; direct assignment (`event += func`) works on IronPython, pythonnet 2.x, and pythonnet 3.x. Also fixed `FileNotFoundError` → `IOError` in Mass Stats scripts for IronPython compatibility, and moved `#! python3` engine header to line 1 so pyRevit 6.4 correctly routes those scripts to CPython.
- True North Updater: angle was written as the clockwise value (e.g. 342.8°) instead of the correct counter-clockwise value (e.g. 17.2°). Fixed sign convention in `_get_true_north_angle_for_view`.
- Distribution repo now receives a matching `V{version}` git tag on every publish, so pyRevit's extension manager displays the correct version instead of the previous tag.

### Changed
- Update WWPTools: button now available without an open Revit document (`zero-doc` context).
- `DeleteSheetSet`: fixed parameter list showing unrelated view parameters.
- Sheets-Views Manager pulldown: reordered by function group.
- Export2Ex and Beta: renamed Project Info parameter to `! P_STATS_Export_Text`.
- Export2Ex: added Project Info settings sync and named save sets.
- Script usage logging (Python): rewired to call C# ScriptLogger directly via CLR -- no separate Vercel POST endpoint required.

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
