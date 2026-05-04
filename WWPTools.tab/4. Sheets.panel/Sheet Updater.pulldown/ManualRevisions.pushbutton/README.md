# WWP Manual Revisions — PyRevit Tool

## What it does
Swaps selected sheets from their current titleblock to a target titleblock that
uses fake revision parameters. The tool automatically builds the four
multiline text parameters from each sheet's actual Revit revisions.

Line count rule:
- Each revision counts as 1 line by default
- Revision descriptions wrap to the next line at about 25 characters without breaking words
- Each wrapped line counts toward the 11-line limit
- Up to 10 wrapped lines are filled on the left side before overflowing to the
  right side
- You can optionally ignore sheets whose real revision table still fits within
  10 wrapped lines, so they stay on the current titleblock

---

## Parameter Mapping

| Parameter Name                  | Type    | Notes                          |
|---------------------------------|---------|--------------------------------|
| ! S_TB_RevisonDate_Left         | Text    | Default left date parameter on the sheet |
| ! S_TB_RevisonDetail_Left       | Text    | Default left description parameter on the sheet |
| ! S_TB_RevisonDate_Right        | Text    | Default right date parameter on the sheet |
| ! S_TB_RevisonDetail_Right      | Text    | Default right description parameter on the sheet |

The tool exposes dropdowns for the four sheet text parameters. The selected
mapping is saved per project and reused on the next run. The names above are
the default selections. When you run the tool, the four text parameters are
populated automatically from the revisions placed on each sheet.

---

## Titleblock Setup

1. Keep your current titleblock with the real Revit revision schedule and create
   a target titleblock with the manual revision labels.
2. In the target titleblock family, add the parameters you want to drive as **instance** parameters.
3. Place **Label** elements for the four manual revision parameters inside the
   manual revision table box.
4. The target titleblock should show the fake revision labels by default.
5. The source titleblock should keep the real Revit revision schedule.

---

## Usage

- Run the tool from the WWP tab
- Confirm or change the four parameter dropdown mappings at the top
- Choose the target titleblock
- Optionally enable the ignore rule for sheets that do not need two columns
- Select one or more sheets inside the dialog
- The current sheet is shown at the top of the list
- Review the titleblock swap summary for the selected sheets
- Click **Update Sheets**

For multiple sheets: each sheet gets its own generated revision dates and
descriptions. Any current titleblock on those sheets is swapped to the selected
target titleblock before the fake revision parameters are written. If the
ignore option is enabled, sheets whose real revision table still fits within
10 wrapped lines are skipped and reported as ignored.

---

## Installation

Drop the `WWP_ManualRevisions.pushbutton` folder into your PyRevit
extension's panel folder, e.g.:

```
WWP.extension/
  WWP.tab/
    Sheets.panel/
      WWP_ManualRevisions.pushbutton/
        script.py
        bundle.yaml
        icon.png   ← optional 32x32 PNG
```

Reload PyRevit.
