# Tableau Workbook Auto-Documentation Skill

**Document to Protect** — Auto-generate structured documentation captions for every worksheet in a Tableau workbook through AI agents.

Give your AI coding assistant a `.twb` or `.twbx` file. Get back a fully documented workbook — every worksheet captioned with its purpose, filters, shelf contents, marks, table calcs, formatting, and dashboard actions — ready to open in Tableau Desktop.

> A future developer should be able to **recreate any sheet from its caption**, or **identify if changes have been made** by comparing the sheet to its caption.

---

## What's in This Repository

```
tableau-twb-docs/
├── SKILL.md                                    # Main skill entrypoint — load this into your AI agent
├── README.md
├── LICENSE
└── scripts/
    └── inject_twb_captions_template.py         # Python template with all generic extraction logic
```

---

## How It Works

The AI analyzes your workbook's XML, builds a workbook-specific script from the template, and injects structured captions into every worksheet.

```
Step 1        Step 2           Step 3          Step 4         Step 5
Extract  ──>  Analyze TWB  ──>  Build Script  ──>  Inject  ──>  Validate
(.twbx→.twb)  (fields, calcs,   (fill in ✎       (run script,  (open in
              actions, sheets)   sections)        add Dev Notes) Tableau)
```

### What Gets Documented

Each worksheet caption includes these sections:

| Section | What It Captures |
|---------|-----------------|
| **PURPOSE** | One-liner: what the sheet shows and why it exists |
| **FILTERS** | Dashboard-level vs. sheet-specific, with field descriptions |
| **HIDDEN DATA** | Rows excluded by filters |
| **COLUMNS / ROWS** | Shelf contents with aggregations resolved to readable names; calculated fields include their formula |
| **MARKS** | Mark type, Color/Size/Shape/Detail encodings, custom labels with formatting |
| **TOOLTIP DETAILS** | Full tooltip text with font, size, color, bold/italic annotations |
| **TABLE CALC** | Compute Using direction, Specific Dimensions with checked fields, sort order |
| **STYLE/FORMATTING** | Cell, header, label, table styles |
| **DASHBOARD ACTIONS** | Highlight, URL, and filter actions that affect the sheet |

A **Developer Notes** worksheet is also added with instructions on how to access the captions.

### The Template Script

The `inject_twb_captions_template.py` has two layers:

- **Generic** (~400 lines) — field resolution, shelf parsing, action indexing, table calc extraction, caption XML building, Developer Notes injection. Works on any Tableau workbook unchanged.
- **Workbook-specific** (~30 lines, marked `✎`) — paths, purpose descriptions, field descriptions, common filter names, dashboard mapping. The AI fills these in after analyzing your workbook.

---

## Setup

### GitHub Copilot

Copy `tableau-twb-docs/` into your workspace's `.github/skills/` directory:

```
your-workspace/
└── .github/
    └── skills/
        └── tableau-twb-docs/
            ├── SKILL.md
            └── scripts/
```

The skill auto-triggers when you mention documenting a Tableau workbook.

### Cursor

Copy `tableau-twb-docs/` into your project's `.cursor/skills/` directory:

```
your-project/
└── .cursor/
    └── skills/
        └── tableau-twb-docs/
            ├── SKILL.md
            └── scripts/
```

### Claude Code

Add to your project's `CLAUDE.md`:

```markdown
## Tableau Documentation Skill
- Skill entrypoint: `tableau-twb-docs/SKILL.md`
- Follow the 9-step process defined in SKILL.md for all workbook documentation.
```

### Other AI Tools (Windsurf, Codex, etc.)

Point your agent at `tableau-twb-docs/SKILL.md` as a context file. The procedure and template script are tool-agnostic.

### No AI

Read `SKILL.md` for the procedure, fill in the `✎ WORKBOOK-SPECIFIC` sections in `scripts/inject_twb_captions_template.py`, and run it with Python.

---

## Compatibility

- Tableau Desktop (any version that supports `.twb` XML format)
- Python 3.6+ (standard library only — no pip installs)
- Works with any data connection type (`sqlproxy`, `federated`, Excel, etc.)

---

## Example Prompt

```
Document this Tableau workbook: Sales_Dashboard.twbx

It has 3 dashboards: Overview, Regional Detail, and Product Breakdown.
The data source is connected to our data warehouse via a published datasource.

Here's a quick summary of the dashboards:
- Overview: KPI cards + trend lines for total sales, profit, and order count
- Regional Detail: Map + bar charts broken down by region and state
- Product Breakdown: Category/sub-category tables with profit ratios
```

The AI extracts the `.twbx`, analyzes all worksheets, builds a tailored script with purpose descriptions, injects captions, adds a Developer Notes sheet, and delivers a documented `.twb` + `.twbx`.

---

## Example Caption Output

```
PURPOSE: Bar chart showing total sales by region, compared to prior year target.

FILTERS:
Common filters (dashboard-level):
        [FILTER: Selected Year]: 2024 — Scopes to the year chosen in the parameter.
Sheet-specific:
        [Category]: Furniture, Technology

COLUMNS:
SUM([Sales])
SUM([Prior Year Sales])  ::  SUM(IF [Year] = [Selected Year]-1 THEN [Sales] END)

ROWS:
[Region]
[Category]

MARKS:
        Mark Type — Bar
        Color — [Region]
        Tooltip — [Profit Ratio]

TABLE CALC CONFIGURATION:
        [Running Total Sales]: Compute Using Specific Dimensions
            Checked: Category, Sub-Category, Region
            Sort order: Specific Dimensions (by [WINDOW_MAX Sales])

STYLE/FORMATTING:
        header: font-size=10, font-weight=bold
        cell: text-align=left
```

---

## License

MIT — see [LICENSE](LICENSE).
