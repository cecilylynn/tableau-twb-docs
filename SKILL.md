---
name: tableau-twb-docs
description: "Auto-document Tableau workbooks by injecting structured captions into every worksheet. Use when: user asks to document a .twb or .twbx workbook, add captions to worksheets, generate sheet-level documentation, or create self-documenting Tableau dashboards. Extracts filters, shelves, marks, tooltips, labels, table calcs, styles, and dashboard actions from the XML and writes human-readable captions."
argument-hint: "Path to the .twb or .twbx file to document"
---

# Tableau Workbook Auto-Documentation

Inject structured documentation captions into every worksheet of a Tableau .twb workbook. Each caption describes the sheet's purpose, configuration, and design decisions so a future developer can recreate or audit it.

## When to Use

- User asks to "document" a Tableau workbook
- User wants captions, descriptions, or sheet-level documentation added to a .twb/.twbx
- User wants self-documenting dashboards

## End-to-End Procedure

### Step 1: Extract the .twb

If the user provides a `.twbx`, extract it (it's a ZIP):

```python
import zipfile
with zipfile.ZipFile("workbook.twbx", "r") as zf:
    zf.extractall("twbx_extract/")
# The .twb and any Image/ folders are now in twbx_extract/
```

### Step 2: Gather Context from the User

Before generating captions, ask:
1. Is there a FAQ, data dictionary, or README that explains what the dashboards do?
2. Are there any workbook-specific naming conventions (e.g., `BAN:` = big number, `KANBAN:` = card layout)?
3. Should the PURPOSE section be auto-generated from sheet names, or does the user want to provide custom descriptions?

### Step 3: Build a Caption Injection Script

Create a Python script that:

1. **Registers the Tableau namespace** (CRITICAL — must be before any ET.parse):
   ```python
   import xml.etree.ElementTree as ET
   ET.register_namespace("user", "http://www.tableausoftware.com/xml/user")
   ```

2. **Builds lookup maps**:
   - **Field map**: `column[@name] → column[@caption]` — resolves internal names like `Calculation_XXXX` to human names
   - **Formula map**: `column[@caption] → calculation[@formula]` — for describing what calculated fields do
   - **Action index**: maps each worksheet name to the dashboard actions that affect it
   - **Parent map**: `{child: parent for parent in root.iter() for child in parent}` — needed for table-calc extraction to walk up to the owning field

3. **For each worksheet**, extracts and generates these caption sections (in order):

| Section | Source | Notes |
|---------|--------|-------|
| **PURPOSE** | User-provided or inferred from sheet name | One-liner: what and why |
| **FILTERS** | `view > filter` elements | Split into "Common (dashboard-level)" vs "Sheet-specific" |
| **HIDDEN DATA** | `groupfilter[@function="except"]` | Values filtered out |
| **COLUMNS** | `table > cols` text | Resolve field names, include aggregation. For calculated fields, append formula via `formula_map` so the sheet can be recreated. |
| **ROWS** | `table > rows` text | Same as columns |
| **MARKS** | `pane > mark[@class]` + `pane > encodings > *` | Mark Type, then per-encoding lines: Color, Size, Shape, Wedge Size, Detail, Label, Tooltip |
| **TOOLTIP DETAILS** | `customized-tooltip > formatted-text > run` | Include formatting metadata: `[FontName, 14pt, Bold, #color]` |
| **TABLE CALC** | `.//table-calc` inside `<column-instance>` (shelf-level only) | Include aggregation (`derivation` attr → CNTD, SUM, etc.), table calc type (`type` attr → % of Total, Running Total, etc.), compute direction, Specific Dimensions with checked fields. Ignore calc-level table-calcs (inside `<calculation>`). |
| **STYLE/FORMATTING** | `style-rule > format` elements | Cell, header, label, table formatting |
| **DASHBOARD ACTIONS** | `root.findall(".//action")` | Type, trigger, target, exclude-sheet logic |

4. **Injects the caption** into `worksheet > layout-options > caption > formatted-text > run`:
   - `layout-options` must be the **first child** of `<worksheet>` — create it if missing
   - Do NOT put captions in `<table><view>` — that causes D2E8DA72 validation error

5. **Post-write fixes** (after `tree.write()`):
   - Replace `ns0:` → `user:` and `xmlns:ns0=` → `xmlns:user=` (ElementTree namespace mangling)
   - Fix XML declaration spacing if needed

### Step 4: Write the Caption Sections

Use the caption formatting pattern (formatted-text runs with attributes):

```python
def _run(text, bold=False, italic=False, color=None):
    a = {}
    if bold:   a["bold"] = "true"
    if italic: a["italic"] = "true"
    if color:  a["fontcolor"] = color
    r = ET.Element("run", a)
    r.text = text
    return r

def _br():
    r = ET.Element("run")
    r.text = "\u00c6\n"  # Tableau line break
    return r
```

**Color conventions**:
- `#000000` — body text, headers (bold)
- `#787878` — attribution ("Documentation generated using the tableau-twb-docs skill, created by Cecily Santiago")
- `#d80937` — warnings
- `#2d59a8` — informational notes

**First two lines of every caption** (standard header):
```
Use Ctrl+E to open sheet description for more details. [italic, black]
Documentation generated using the tableau-twb-docs skill, created by Cecily Santiago (github.com/cecilylynn). [italic, gray]
```

### Step 5: Resolve Field References

Internal field names use this pattern: `[datasource].[agg:Calculation_XXXX:qk]`

Resolution strategy:
1. Build map from `<column name="[Calculation_XXXX]" caption="Human Name">` across all datasources
2. Strip datasource prefix and aggregation qualifiers to find the column name
3. Look up in map; if not found and starts with `Calculation_`, leave as-is; otherwise title-case the snake_case name

For **action captions**: resolve `<ATTR([ds].[col])>` patterns using the same field map.

For **tooltip/label formatted text**: resolve `<[ds].[col]>` references inline, and capture formatting attributes (`bold`, `fontcolor`, `fontsize`, `fontname`, `italic`, `underline`) per run.

For **COLUMNS / ROWS shelf fields**: after resolving the field name, check `formula_map` — if the field is a calculated field, append its formula with `::` separator so a future developer can recreate the calc. Use `_resolve_formula()` to replace any `[Calculation_xxx]` refs in the formula with their captions. Example output: `AGG([Margin %])  ::  SUM([Revenue]) / SUM([Cost])`

### Step 5b: Extract Table Calc Configuration

Table calc settings are stored as `<table-calc>` elements. There are **two locations** and you should only document the shelf-level ones:

- **Calc-level** (parent is `<calculation>`, grandparent is `<column>`): The formula's *default* compute direction. These are internal to the calculated field definition — **do NOT include these in captions** since they describe formula internals, not user-visible configuration.
- **Shelf-level** (parent is `<column-instance>`): The actual "Compute Using" the user configured in the Table Calculation dialog. **Only document these.**

Use the **parent map** to walk up from each `<table-calc>` and determine which kind it is:

```python
def _resolve_table_calc(tc, parent_map, fm):
    parent = parent_map.get(tc)
    if parent.tag == "calculation":   # calc-level → skip
        kind = "calc"
    elif parent.tag == "column-instance":  # shelf-level → document
        kind = "shelf"
        field_name = resolve(parent.get("column", ""), fm)
        derivation = parent.get("derivation", "")  # e.g. CountD, Sum
    tc_type = tc.get("type", "")  # e.g. PctTotal, RunTotal
```

The **`derivation`** attribute on the `column-instance` tells you the aggregation (CountD → CNTD, Sum → SUM, etc.). The **`type`** attribute on the `<table-calc>` element tells you the table calc type (PctTotal → % of Total, RunTotal → Running Total, etc.). Both must be included in the caption output.

The `ordering-type` attribute maps to Tableau UI labels:

| XML `ordering-type` | Tableau UI label |
|---------------------|------------------|
| `Rows` | Table (down) |
| `Columns` | Table (across) |
| `Field` | **Specific Dimensions** |

When `ordering-type="Field"`, the `<order>` child elements list the **checked dimensions**:

```xml
<column-instance column="[Calc_123]" derivation="CountD" ...>
  <table-calc type="PctTotal" ordering-type="Rows" />
</column-instance>
```

Caption output should look like:
```
% of Total of CNTD([Order ID]): Compute Using Table (down)
```

Or for Specific Dimensions:
```
Running Total of SUM([Revenue]): Compute Using Specific Dimensions
    Checked: Category, Sub-Category, Region
    Sort order: Specific Dimensions (by [WINDOW_MAX Revenue])
```

For fields using `ordering-type="Field"`, also resolve the `<order>` field references through the field map. Use `_resolve_bare()` which handles both `[ds].[col]` patterns and bare `[Calculation_xxx]` lookups.

### Step 6: Extract Dashboard Actions

Actions are at the **workbook root level**, NOT inside `<dashboard>` elements:

```python
for action in root.findall(".//action"):
    # ...
```

Action types:
- **Highlight**: has `<command command="tsc:brush">` with `<param name="field-captions">`
- **URL**: has `<link expression="...">` — check for external URLs, mailto links, etc.
- **Filter**: has `<command command="tsc:filter">`

Source targeting:
- `<source type="all">` + `<exclude-sheet>` = applies to all sheets NOT excluded
- `<source type="sheet" dashboard="X">` = all sheets on that dashboard
- `<source worksheet="X">` = single sheet

### Step 7: Repackage as .twbx

If the original was a `.twbx`, repackage with embedded images:

```python
import zipfile
with zipfile.ZipFile("output.twbx", "w", zipfile.ZIP_DEFLATED) as zf:
    zf.write("documented.twb", "documented.twb")
    # Include any Image/ folder from the original extraction
    zf.write("Image/logo.svg", "Image/logo.svg")
```

### Step 8: Add a Developer Notes Sheet

After injecting captions, add a **Developer Notes** worksheet that tells future developers how to access the documentation. This sheet uses the text-mark pattern:

1. Create a calculated field `''` (empty string) in the datasource
2. Put it on the **Text** encoding shelf in the marks card
3. Set `mark-labels-show=true` in the pane style
4. Use `customized-label > formatted-text` for the actual message content

**Important**: Do NOT use `show-labels` as an attribute on `customized-label` — that causes D2E8DA72. Use a `style-rule` inside the pane instead:
```xml
<style>
  <style-rule element="mark">
    <format attr="mark-labels-show" value="true" />
    <format attr="mark-labels-cull" value="true" />
  </style-rule>
</style>
```

The Developer Notes text should include:
- "This workbook is documented using auto-generated captions."
- Instructions: navigate to any sheet, then **Worksheet menu > Show Caption** (shortcut: Alt, W, A, Enter)
- List of what each caption contains (Purpose, Filters, Hidden Data, Columns, Rows, Marks, etc.)
- "A future developer should be able to recreate any sheet from its caption or identify if changes have been made."
- "Documentation generated using the tableau-twb-docs skill, created by Cecily Santiago (github.com/cecilylynn)."

The Developer Notes sheet is built into the template script's `add_developer_notes()` function. See [inject_twb_captions_template.py](./scripts/inject_twb_captions_template.py).

### Step 9: Validate

1. Count worksheets with captions — should match total worksheet count
2. Verify `user:` prefix is preserved (not `ns0:`)
3. Open in Tableau Desktop — no D2E8DA72 or 2805CF18 errors
4. Spot-check 2–3 captions via right-click → Description (Ctrl+E)

## Critical Pitfalls

| Pitfall | Consequence | Fix |
|---------|-------------|-----|
| Caption in `<table><view>` | D2E8DA72 error, workbook won't open | Place in `<layout-options>` as first child of `<worksheet>` |
| Missing `ET.register_namespace("user", ...)` | `user:unnamed` → `ns0:unnamed`, breaks shelf-scoped ad-hoc calcs | Register before any `ET.parse()` call |
| Searching for actions inside `<dashboard>` | Finds 0 actions | Search `root.findall(".//action")` at workbook level |
| Not resolving `<ATTR([ds].[col])>` in action captions | Raw datasource refs in output like `<ATTR([datasource...].[field_name])>` | Regex-replace angle-bracket refs using field map |
| `show-labels` attribute on `customized-label` | D2E8DA72 error | Use `style-rule > format[@attr="mark-labels-show"]` inside the pane `<style>` instead |
| Including calc-level `<table-calc>` in captions | Caption lists internal formula defaults that aren't on the sheet — confusing and wrong | Only document shelf-level table-calcs (parent is `<column-instance>`). Ignore calc-level (parent is `<calculation>`). |
| Showing `ordering-type` raw XML values | `Rows`, `Columns`, `Field` mean nothing to a Tableau practitioner | Map to UI labels: Rows→Table (down), Columns→Table (across), Field→Specific Dimensions |
| Table calc missing aggregation and type | `[Order ID]: Compute Using Table (down)` — should be `% of Total of CNTD([Order ID])` | Read `derivation` attr from `column-instance` (CountD→CNTD, Sum→SUM) and `type` attr from `<table-calc>` (PctTotal→% of Total, RunTotal→Running Total) |
| Nested shelf aggregation qualifiers not resolved | `pcto:ctd:order_id:qk` resolves to raw text instead of `% of Total of CNTD([Order ID])` | `resolve_shelf()` must iteratively strip agg prefixes (pcto, ctd, sum, etc.) and join them with " of " |
| Calculated fields on shelves shown without formula | `AGG([donut])` is not enough to recreate the sheet — you need to know what the calc does | Look up field name in `formula_map`; if found, append `  ::  {resolved_formula}` after the field display |
| Ad-hoc shelf calcs missing from `formula_map` | `build_formula_map` only scans `<datasource>` but ad-hoc calcs (//rename trick) live in `<datasource-dependencies>` inside worksheets | Scan both `root.findall(".//datasource")` AND `root.findall(".//datasource-dependencies")` for columns with formulas |

## Template Script

See [inject_twb_captions_template.py](./scripts/inject_twb_captions_template.py) — a complete, ready-to-adapt script with all generic extraction logic built in. The workbook-specific sections are clearly marked with `✎` comments at the top of the file:

1. Set `IN_TWB`, `OUT`, `OUT_X`, `IMG`, and `DS_NAME` paths
2. Fill in `_PURPOSE` — one-liner per worksheet explaining what it shows and why
3. Fill in `_FIELD_DESC` — optional descriptions for key calculated fields
4. Fill in `COMMON_FILTER_NAMES` — filters that appear on most/all sheets (dashboard-level)
5. Update `dashboard_of()` — map worksheet names to their parent dashboard

All extraction functions (`build_field_map`, `resolve`, `gen_sections`, `build_action_index`, `add_developer_notes`, etc.) are generic and work on any Tableau workbook.
