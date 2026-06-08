"""
inject_twb_captions_template.py — Generic Tableau Workbook Auto-Documentation

Injects structured documentation captions into every worksheet of a .twb file.
Each caption describes the sheet's purpose, configuration, and design decisions
so a future developer can recreate or audit it.

Sections per worksheet:
  PURPOSE · FILTERS · HIDDEN DATA · COLUMNS · ROWS ·
  MARKS · TOOLTIP DETAILS · TABLE CALC · STYLE/FORMATTING · DASHBOARD ACTIONS

USAGE:
  1. Fill in the WORKBOOK-SPECIFIC section below (marked with ✎) for your workbook.
  2. Run:  python inject_twb_captions_template.py
"""

import xml.etree.ElementTree as ET
import re, os, uuid, zipfile

# CRITICAL: register before any ET.parse() to prevent ns0: mangling
ET.register_namespace("user", "http://www.tableausoftware.com/xml/user")

BASE = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════
# ✎ WORKBOOK-SPECIFIC — edit these for each new workbook
# ═══════════════════════════════════════════════════════

IN_TWB = os.path.join(BASE, "path", "to", "Your Workbook.twb")   # ✎ source .twb
OUT    = os.path.join(BASE, "Your Workbook - Documented.twb")     # ✎ output .twb
OUT_X  = os.path.join(BASE, "Your Workbook - Documented.twbx")    # ✎ output .twbx (optional)
IMG    = os.path.join(BASE, "path", "to", "Image", "logo.svg")    # ✎ embedded image (optional)

# Datasource name for the Developer Notes sheet — find in the TWB XML under
# <datasources><datasource name="your.datasource.name" caption="...">
DS_NAME = "your.datasource.name"  # ✎ your datasource internal name

# ✎ PURPOSE — one-liner describing what each worksheet does.
#   Key = worksheet name (case-sensitive), Value = description string.
#   A future developer should be able to understand the sheet's role from this alone.
_PURPOSE = {
    # "Sheet Name": "What this sheet shows and why it exists.",
    # "Sales by Region": "Bar chart showing total sales by region for the selected year.",
    # "Profit Trend": "Line chart showing monthly profit trend over time.",
}

# ✎ FIELD DESCRIPTIONS — optional descriptions for key calculated fields.
#   These appear inline next to the field name in FILTERS, MARKS, etc.
_FIELD_DESC = {
    # "Field Caption": "What this field does / why it exists.",
    # "FILTER: Selected Year": "Scopes to the year chosen in the parameter.",
    # "Is Current Year?": "TRUE when the selected year equals the max year in the data.",
}

# ✎ COMMON FILTER NAMES — filters that appear on most/all sheets (dashboard-level).
#   Listed separately in captions under "Common filters (dashboard-level)".
COMMON_FILTER_NAMES = {
    # "FILTER: Selected Year",
    # "FILTER: Selected Source",
}

def dashboard_of(name):
    """✎ Map worksheet name → parent dashboard name.
    Used in console output and can be referenced in PURPOSE if needed."""
    # Example pattern-based mapping:
    # u = name.upper()
    # if u.startswith("KPI "): return "Overview"
    # if u.startswith("TABLE "): return "Detail Tables"
    # if u == "TIMESTAMP": return "All dashboards"
    return "Dashboard"

def purpose_of(name):
    """Return the PURPOSE text for a worksheet."""
    u = name.upper()
    if u in _PURPOSE: return _PURPOSE[u]
    # Case-insensitive fallback
    for k, v in _PURPOSE.items():
        if k.upper() == u: return v
    return f"Part of the {dashboard_of(name)} dashboard."

def describe_field(name):
    """Return an optional description for a field (empty string if unknown)."""
    if name in _FIELD_DESC: return _FIELD_DESC[name]
    return ""

# ═══════════════════════════════════════════════════════
# GENERIC — everything below works on any Tableau workbook
# ═══════════════════════════════════════════════════════

# ── Lookup helpers ──

def build_field_map(root):
    """Build {column[@name]: column[@caption]} across all datasources."""
    m = {}
    for c in root.iter("column"):
        n, cap = c.get("name", ""), c.get("caption", "")
        if cap and n:
            m[n] = cap
    return m

def build_formula_map(root):
    """Build {caption: first line of formula} for calculated fields.
    Scans both <datasource> and <datasource-dependencies> (worksheet-scoped ad-hoc calcs)."""
    m = {}
    for parent in list(root.findall(".//datasource")) + list(root.findall(".//datasource-dependencies")):
        for col in parent.findall("column"):
            cap = col.get("caption", "")
            calc = col.find("calculation")
            if calc is not None and cap:
                raw = calc.get("formula", "")
                lines = [l.strip() for l in raw.split("\n")
                         if l.strip() and not l.strip().startswith("//")]
                if lines:
                    m.setdefault(cap, lines[0][:200])  # first occurrence wins
    return m

def resolve(raw, fm):
    """Resolve a [datasource].[agg:col:type] reference to a human-readable name."""
    if not raw: return ""
    match = re.search(r'\.\[([a-z]+:)?([^:\]]+)(:[a-z]+)?\]$', raw)
    if match:
        col = match.group(2)
        lk = f"[{col}]"
        if lk in fm: return fm[lk]
        if col.startswith("Calculation_"): return col
        return col.replace("_", " ").title()
    return raw.split("].[")[-1].rstrip("]") if "].[" in raw else raw

def agg_label(qualifier):
    """Map Tableau aggregation qualifier to display label."""
    return {"sum": "SUM", "avg": "AVG", "min": "MIN", "max": "MAX",
            "cnt": "CNT", "ctd": "CNTD", "attr": "ATTR", "none": "",
            "usr": "AGG", "pcto": "% of Total", "tmn": "MONTH",
            "tqr": "QUARTER", "tyr": "YEAR", "tdy": "DAY"
            }.get(qualifier, qualifier.upper())

def resolve_shelf(raw, fm):
    """Resolve a rows/cols shelf string to a list of (name, aggregation) tuples.
    Handles nested aggregation qualifiers like pcto:ctd:field:qk → '% of Total of CNTD'."""
    if not raw: return []
    fields = []
    for ds, cp in re.findall(r'\[([^\]]+)\]\.\[([^\]]+)\]', raw):
        # Handle nested aggregation qualifiers like pcto:ctd:field:qk
        inner = cp
        agg_parts = []
        while True:
            am = re.match(r'([a-z]+):(.+)', inner)
            if am and am.group(1) in ("sum","avg","min","max","cnt","ctd","attr",
                                      "none","usr","pcto","tmn","tqr","tyr","tdy"):
                agg_parts.append(agg_label(am.group(1)))
                inner = am.group(2)
            else:
                break
        # Strip trailing type qualifier (e.g. :qk, :nk, :ok)
        inner = re.sub(r':[a-z]+$', '', inner)
        # Resolve the bare field name
        lk = f"[{inner}]"
        if lk in fm:
            name = fm[lk]
        elif inner.startswith("Calculation_"):
            name = inner
        else:
            name = inner.replace("_", " ").title()
        # Build aggregation string: "% of Total of CNTD" for pcto:ctd
        if agg_parts:
            agg = " of ".join(p for p in agg_parts if p)
        else:
            agg = ""
        fields.append((name, agg))
    return fields

def _resolve_bare(raw, fm):
    """Resolve a bare [FieldName] or [ds].[col] reference via the field map."""
    if not raw: return ""
    if raw in fm: return fm[raw]
    resolved = resolve(raw, fm)
    if resolved.startswith("Calculation_") or resolved.startswith("[Calculation_"):
        bare = f"[{resolved}]" if not resolved.startswith("[") else resolved
        if bare in fm: return fm[bare]
    return resolved

def _resolve_formula(formula, fm):
    """Replace [Calculation_xxx] refs in a formula string with their captions."""
    def _repl(m):
        ref = m.group(0)
        if ref in fm: return f"[{fm[ref]}]"
        return ref
    return re.sub(r'\[Calculation_[^\]]+\]', _repl, formula)

def _resolve_action_caption(raw, fm):
    """Resolve <ATTR([ds].[col])> patterns in action caption text."""
    def _repl(m):
        inner = m.group(0).lstrip("<").rstrip(">")
        agg_m = re.match(r'[A-Z]+\((.+)\)$', inner)
        if agg_m: inner = agg_m.group(1)
        return resolve(inner, fm)
    return re.sub(r'<[^>]+>', _repl, raw)


# ── Caption XML helpers ──

def _run(text, bold=False, italic=False, color=None, underline=False):
    a = {}
    if bold:      a["bold"] = "true"
    if italic:    a["italic"] = "true"
    if color:     a["fontcolor"] = color
    if underline: a["underline"] = "true"
    r = ET.Element("run", a); r.text = text; return r

def _br():
    r = ET.Element("run"); r.text = "\u00c6\n"; return r

def build_caption(lines):
    """Build a <caption> element from a list of (kind, text) tuples."""
    cap = ET.Element("caption")
    ft = ET.SubElement(cap, "formatted-text")
    ft.append(_run("Use Ctrl+E to open sheet description for more details.",
                   italic=True, color="#000000"))
    ft.append(_br())
    ft.append(_run("Documentation generated by GitHub Copilot based on workbook structure.",
                   italic=True, color="#787878"))
    ft.append(_br()); ft.append(_br())
    for k, t in lines:
        if   k == "hdr":      ft.append(_run(t, bold=True, color="#000000")); ft.append(_run(": ", color="#000000"))
        elif k == "hdr_line": ft.append(_run(t, bold=True, color="#000000")); ft.append(_run(":", color="#000000")); ft.append(_br())
        elif k == "txt":      ft.append(_run(t, color="#000000")); ft.append(_br())
        elif k == "detail":   ft.append(_run(t, color="#000000")); ft.append(_br())
        elif k == "ital":     ft.append(_run(t, italic=True, color="#000000")); ft.append(_br())
        elif k == "warn":     ft.append(_run(t, bold=True, color="#d80937")); ft.append(_br())
        elif k == "info":     ft.append(_run(t, color="#2d59a8")); ft.append(_br())
        elif k == "ul":       ft.append(_run(t, underline=True, color="#000000")); ft.append(_br())
        elif k == "brk":      ft.append(_br())
    return cap


# ── Action index ──

def build_action_index(root, fm):
    """Return {worksheet_name: [action_description_string, ...]}"""
    all_ws = {ws.get("name", "") for ws in root.findall(".//worksheet")}
    idx = {}

    for action in root.findall(".//action"):
        caption = _resolve_action_caption(action.get("caption", ""), fm)
        name = action.get("name", "")
        cmd_elem = action.find("command")
        activation = action.find("activation")
        link = action.find("link")

        if cmd_elem is not None:
            field_caps = ""
            for p in cmd_elem.findall("param"):
                if p.get("name") == "field-captions":
                    field_caps = p.get("value", "")
            act_desc = f"{caption or name} (Highlight Action): Target Highlighting on [{field_caps}]."
            trigger = activation.get("type", "") if activation is not None else ""
            if trigger:
                act_desc += f" Run action on: {trigger.replace('on-', '').capitalize()}."
            if activation is not None and activation.get("auto-clear") == "true":
                act_desc += " Clearing will reset."
        elif link is not None:
            expr = link.get("expression", "")
            act_desc = f"{caption} (URL Action): {expr}"
        else:
            act_desc = f"{caption or name}: (action)"

        source = action.find("source")
        if source is not None:
            src_type = source.get("type", "")
            if src_type == "all":
                excludes = {e.get("name", "") for e in source.findall("exclude-sheet")}
                included = all_ws - excludes
            elif source.get("worksheet"):
                included = {source.get("worksheet")}
            else:
                included = set()
        else:
            included = set()

        for ws in included:
            idx.setdefault(ws, []).append(act_desc)

    return idx


# ── Table calc extraction ──

def _ordering_label(ot):
    """Map XML ordering-type to Tableau UI label."""
    return {"Rows": "Table (down)", "Columns": "Table (across)",
            "Field": "Specific Dimensions"}.get(ot, ot)

def _derivation_label(deriv):
    """Map column-instance derivation attr to Tableau aggregation label."""
    return {"Sum": "SUM", "Avg": "AVG", "Min": "MIN", "Max": "MAX",
            "Count": "CNT", "CountD": "CNTD", "Counta": "CNTA",
            "Attr": "ATTR", "None": "", "User": "AGG",
            }.get(deriv, deriv)

def _tc_type_label(tc_type):
    """Map table-calc type attr to Tableau UI label."""
    return {"PctTotal": "% of Total", "RunTotal": "Running Total",
            "PctDiff": "% Difference", "Diff": "Difference",
            "PctFrom": "% From", "MovAvg": "Moving Average",
            "RunAvg": "Running Average", "Rank": "Rank",
            "Percentile": "Percentile",
            }.get(tc_type, tc_type)

def _resolve_table_calc(tc, parent_map, fm):
    """Classify a table-calc as calc-level or shelf-level and extract details.
    Returns (kind, field_name, formula, ordering, tc_field, order_children, derivation, tc_type)."""
    parent = parent_map.get(tc)
    kind = ""
    field_name = ""
    formula = ""
    derivation = ""
    tc_type = tc.get("type", "")  # e.g. PctTotal, RunTotal
    if parent is not None:
        if parent.tag == "calculation":
            kind = "calc"
            formula = _resolve_formula(parent.get("formula", ""), fm)
            gp = parent_map.get(parent)
            if gp is not None and gp.tag == "column":
                field_name = gp.get("caption", "") or _resolve_bare(gp.get("name", ""), fm)
        elif parent.tag == "column-instance":
            kind = "shelf"
            col_ref = parent.get("column", "")
            field_name = _resolve_bare(col_ref, fm) if col_ref else ""
            derivation = parent.get("derivation", "")

    ordering = tc.get("ordering-type", "")
    tc_field = _resolve_bare(tc.get("field", ""), fm)
    order_children = [_resolve_bare(o.get("field", ""), fm) for o in tc.findall("order")]

    return kind, field_name, formula, ordering, tc_field, order_children, derivation, tc_type


# ── Formatted block extraction (tooltips, labels) ──

def _extract_formatted_block(ws, tag_name, fm):
    """Extract customized-tooltip or customized-label as a list of dicts with formatting."""
    for ct in ws.iter(tag_name):
        ft = ct.find("formatted-text")
        if ft is None: continue
        lines = []
        for r in ft.findall("run"):
            text = r.text or ""
            for ds, cp in re.findall(r'<\[([^\]]+)\]\.\[([^\]]+)\]>', text):
                full = f"[{ds}].[{cp}]"
                resolved = resolve(full, fm)
                text = text.replace(f"<[{ds}].[{cp}]>", f"[{resolved}]")
            segments = re.split(r'\u00c6\s*\n', text)
            for i, seg in enumerate(segments):
                seg_clean = seg.strip()
                if seg_clean:
                    entry = {"text": seg_clean}
                    if r.get("bold"):       entry["bold"] = True
                    if r.get("italic"):     entry["italic"] = True
                    if r.get("underline"):  entry["underline"] = True
                    if r.get("fontcolor"):  entry["fontcolor"] = r.get("fontcolor")
                    if r.get("fontsize"):   entry["fontsize"] = r.get("fontsize")
                    if r.get("fontname"):   entry["fontname"] = r.get("fontname")
                    lines.append(entry)
                if i < len(segments) - 1:
                    lines.append({"text": "\n"})
        return lines if lines else None
    return None

def _format_block_for_caption(lines):
    """Convert formatted block dicts into caption-ready strings with [font, size, Bold, #color] tags."""
    if not lines: return []
    result = []
    current_parts = []
    for entry in lines:
        if entry["text"] == "\n":
            if current_parts:
                result.append("  ".join(current_parts))
                current_parts = []
            continue
        text = entry["text"]
        fmt_parts = []
        if entry.get("fontname"):   fmt_parts.append(entry["fontname"])
        if entry.get("fontsize"):   fmt_parts.append(f"{entry['fontsize']}pt")
        if entry.get("bold"):       fmt_parts.append("Bold")
        if entry.get("italic"):     fmt_parts.append("Italic")
        if entry.get("underline"):  fmt_parts.append("Underline")
        if entry.get("fontcolor"):  fmt_parts.append(entry["fontcolor"])
        if fmt_parts:
            fmt_tag = f" [{', '.join(fmt_parts)}]"
        else:
            fmt_tag = ""
        current_parts.append(f"{text}{fmt_tag}")
    if current_parts:
        result.append("  ".join(current_parts))
    return result


# ── Section generator ──

def gen_sections(ws, fm, formula_map, action_idx, parent_map):
    """Extract all config from a worksheet and return caption sections."""
    name = ws.get("name", "")
    purpose = purpose_of(name)
    S = []

    table = ws.find("table")
    view = table.find("view") if table is not None else None

    # PURPOSE
    S.append(("hdr", "PURPOSE"))
    S.append(("txt", purpose))
    S.append(("brk", ""))

    if view is None:
        return S

    # FILTERS
    common, specific = [], []
    for filt in view.findall("filter"):
        col = filt.get("column", "")
        resolved = resolve(col, fm)
        members = []
        for gf in filt.findall(".//groupfilter"):
            m = gf.get("member", "")
            fn = gf.get("function", "")
            if m: members.append(m.strip('"'))
            elif fn and fn not in ("union", "filter"): members.append(f"({fn})")
        val_str = ", ".join(members) if members else "(all members)"
        desc = describe_field(resolved)
        line = f"[{resolved}]: {val_str}"
        if desc: line += f" — {desc}"
        if resolved in COMMON_FILTER_NAMES:
            common.append(line)
        else:
            specific.append(line)

    if common or specific:
        S.append(("hdr_line", "FILTERS"))
        if common:
            S.append(("ul", "Common filters (dashboard-level):"))
            for l in common: S.append(("detail", f"        {l}"))
        if specific:
            if common: S.append(("ul", "Sheet-specific:"))
            for l in specific: S.append(("detail", f"        {l}"))
        S.append(("brk", ""))

    # HIDDEN DATA
    hidden = []
    for filt in view.findall("filter"):
        col = filt.get("column", "")
        resolved = resolve(col, fm)
        for gf in filt.findall(".//groupfilter"):
            fn = gf.get("function", "")
            if fn in ("except", "not"):
                m = gf.get("member", "")
                hidden.append(f"[{resolved}]={m.strip(chr(34))} rows are hidden.")
    if hidden:
        S.append(("hdr_line", "HIDDEN DATA"))
        for l in hidden: S.append(("detail", l))
        S.append(("brk", ""))

    # COLUMNS
    S.append(("hdr_line", "COLUMNS"))
    cols_fields = resolve_shelf(
        table.find("cols").text if table.find("cols") is not None and table.find("cols").text else "", fm)
    if not cols_fields:
        S.append(("detail", "(empty)"))
    else:
        for fname, agg in cols_fields:
            display = f"{agg}([{fname}])" if agg else f"[{fname}]"
            desc = describe_field(fname)
            if desc: display += f" — {desc}"
            if fname in formula_map:
                display += f"  ::  {_resolve_formula(formula_map[fname], fm)}"
            S.append(("detail", display))
    S.append(("brk", ""))

    # ROWS
    S.append(("hdr_line", "ROWS"))
    rows_fields = resolve_shelf(
        table.find("rows").text if table.find("rows") is not None and table.find("rows").text else "", fm)
    if not rows_fields:
        S.append(("detail", "(empty)"))
    else:
        for fname, agg in rows_fields:
            display = f"{agg}([{fname}])" if agg else f"[{fname}]"
            desc = describe_field(fname)
            if desc: display += f" — {desc}"
            if fname in formula_map:
                display += f"  ::  {_resolve_formula(formula_map[fname], fm)}"
            S.append(("detail", display))
    S.append(("brk", ""))

    # MARKS
    S.append(("hdr_line", "MARKS"))
    mark_type = "Automatic"
    panes = ws.findall(".//pane")
    for pane in panes:
        mk = pane.find("mark")
        if mk is not None and mk.get("class", "") not in ("", "Automatic"):
            mark_type = mk.get("class")
    S.append(("detail", f"        Mark Type — {mark_type}"))

    seen = set()
    enc_lines = {"color": [], "size": [], "shape": [], "text": [], "tooltip": [],
                 "lod": [], "wedge-size": []}
    for pane in panes:
        encodings = pane.find("encodings")
        if encodings is None: continue
        for enc in encodings:
            tag = enc.tag
            col = enc.get("column", "")
            resolved = resolve(col, fm)
            key = (tag, resolved)
            if key in seen or not resolved: continue
            seen.add(key)
            if tag in enc_lines: enc_lines[tag].append(resolved)

    for f in enc_lines["color"]:
        desc = describe_field(f)
        line = f"        Color — [{f}]"
        if desc: line += f" — {desc}"
        S.append(("detail", line))
    for f in enc_lines["size"]:
        S.append(("detail", f"        Size — [{f}]"))
    for f in enc_lines["shape"]:
        S.append(("detail", f"        Shape — [{f}]"))
    for f in enc_lines["wedge-size"]:
        S.append(("detail", f"        Wedge Size (Angle) — [{f}]"))
    for f in enc_lines["lod"]:
        desc = describe_field(f)
        line = f"        Detail — [{f}]"
        if desc: line += f" — {desc}"
        S.append(("detail", line))

    # Labels
    label_raw = _extract_formatted_block(ws, "customized-label", fm)
    label_lines = _format_block_for_caption(label_raw) if label_raw else []
    styles = {}
    for sr in ws.findall(".//style-rule"):
        elem = sr.get("element", "")
        for fmt in sr.findall("format"):
            styles.setdefault(elem, {})[fmt.get("attr", "")] = fmt.get("value", "")
    mark_style = styles.get("mark", {})

    if label_lines:
        S.append(("detail", "        Label (custom):"))
        for ll in label_lines: S.append(("detail", f"            {ll}"))
    elif enc_lines["text"]:
        S.append(("detail", f"        Text — {', '.join(enc_lines['text'])}"))
    if mark_style.get("mark-labels-show") == "true":
        S.append(("detail", "        Label visibility: Show mark labels"))

    # Tooltip fields
    for f in enc_lines["tooltip"]:
        desc = describe_field(f)
        line = f"        Tooltip — [{f}]"
        if desc: line += f" — {desc}"
        S.append(("detail", line))
    S.append(("brk", ""))

    # TOOLTIP DETAILS
    tooltip_raw = _extract_formatted_block(ws, "customized-tooltip", fm)
    tooltip_lines = _format_block_for_caption(tooltip_raw) if tooltip_raw else []
    tooltip_buttons = None
    for ct in ws.iter("customized-tooltip"):
        if ct.get("show-buttons") == "false": tooltip_buttons = "OFF"
        break
    if tooltip_lines:
        S.append(("hdr_line", "TOOLTIP DETAILS"))
        for tl in tooltip_lines: S.append(("detail", f"        {tl}"))
        if tooltip_buttons: S.append(("detail", f"        Tooltip command buttons: {tooltip_buttons}"))
        S.append(("brk", ""))

    # TABLE CALC — only shelf-level configs (fields actually on the sheet)
    shelf_groups = {}
    for tc in ws.findall(".//table-calc"):
        kind, field_name, formula, ordering, tc_field, order_children, derivation, tc_type = \
            _resolve_table_calc(tc, parent_map, fm)
        if kind == "shelf":
            shelf_groups.setdefault(field_name, []).append((ordering, tc_field, order_children, derivation, tc_type))

    if shelf_groups:
        S.append(("hdr_line", "TABLE CALC CONFIGURATION"))
        for fname, configs in shelf_groups.items():
            specific_dims = None
            default_dir = None
            sort_field = None
            derivation = ""
            tc_type = ""
            for ordering, tc_field, order_children, deriv, tct in configs:
                if deriv: derivation = deriv
                if tct: tc_type = tct
                if ordering == "Field":
                    specific_dims = order_children
                    sort_field = tc_field
                else:
                    default_dir = _ordering_label(ordering)
            # Build display: "% of Total of CNTD([Opportunity ID])"
            agg = _derivation_label(derivation)
            field_display = f"{agg}([{fname}])" if agg else f"[{fname}]"
            tc_label = _tc_type_label(tc_type) if tc_type else ""
            if tc_label:
                field_display = f"{tc_label} of {field_display}"
            if specific_dims:
                S.append(("detail", f"        {field_display}: Compute Using Specific Dimensions"))
                S.append(("detail", f"            Checked: {', '.join(specific_dims)}"))
                if sort_field:
                    S.append(("detail", f"            Sort order: Specific Dimensions (by [{sort_field}])"))
            elif default_dir:
                S.append(("detail", f"        {field_display}: Compute Using {default_dir}"))
        S.append(("brk", ""))

    # STYLE/FORMATTING
    style_lines = []
    for elem in ("cell", "header", "label", "table", "table-div", "pane"):
        if elem in styles:
            vals = ", ".join(f"{k}={v}" for k, v in styles[elem].items()
                            if k not in ("mark-labels-cull", "mark-labels-show"))
            if vals: style_lines.append(f"        {elem}: {vals}")
    if style_lines:
        S.append(("hdr_line", "STYLE/FORMATTING"))
        for l in style_lines: S.append(("detail", l))
        S.append(("brk", ""))

    # DASHBOARD ACTIONS
    actions = action_idx.get(name, [])
    if actions:
        S.append(("hdr_line", "DASHBOARD ACTIONS"))
        S.append(("ital", "Configured from the dashboard, not the sheet."))
        for a in actions:
            S.append(("detail", a))
            S.append(("brk", ""))

    return S


# ── Developer Notes sheet ──

def add_developer_notes(root, ds_name):
    """Add a Developer Notes text sheet explaining how to access captions."""
    worksheets_elem = root.find("worksheets")
    existing = [ws.get("name") for ws in worksheets_elem.findall("worksheet")]
    if "Developer Notes" in existing:
        return

    ws = ET.SubElement(worksheets_elem, "worksheet", {"name": "Developer Notes"})
    lo = ET.SubElement(ws, "layout-options")
    title = ET.SubElement(lo, "title")
    ft_title = ET.SubElement(title, "formatted-text")
    r1 = ET.SubElement(ft_title, "run", {"bold": "true", "fontsize": "24", "fontcolor": "#000000"})
    r1.text = "Developer Notes"

    table = ET.SubElement(ws, "table")
    view = ET.SubElement(table, "view")
    dss = ET.SubElement(view, "datasources")
    ET.SubElement(dss, "datasource", {"name": ds_name})
    deps = ET.SubElement(view, "datasource-dependencies", {"datasource": ds_name})
    col = ET.SubElement(deps, "column", {
        "caption": "Developer Notes Text", "datatype": "string",
        "name": "[Calculation_9999999999999998]", "role": "dimension", "type": "nominal"
    })
    ET.SubElement(col, "calculation", {"class": "tableau", "formula": "''"})
    ET.SubElement(deps, "column-instance", {
        "column": "[Calculation_9999999999999998]", "derivation": "None",
        "name": "[none:Calculation_9999999999999998:nk]", "pivot": "key", "type": "nominal"
    })
    ET.SubElement(view, "aggregation", {"value": "true"})
    ET.SubElement(table, "style")

    panes = ET.SubElement(table, "panes")
    pane = ET.SubElement(panes, "pane", {"selection-relaxation-option": "selection-relaxation-allow"})
    v2 = ET.SubElement(pane, "view")
    ET.SubElement(v2, "breakdown", {"value": "auto"})
    ET.SubElement(pane, "mark", {"class": "Automatic"})
    encodings = ET.SubElement(pane, "encodings")
    ET.SubElement(encodings, "text", {"column": f"[{ds_name}].[none:Calculation_9999999999999998:nk]"})

    cl = ET.SubElement(pane, "customized-label")
    ft = ET.SubElement(cl, "formatted-text")

    def add_line(text, **attrs):
        if not text:
            br = ET.SubElement(ft, "run"); br.text = "\u00c6\n"; return
        r = ET.SubElement(ft, "run", attrs); r.text = text
        br = ET.SubElement(ft, "run"); br.text = "\u00c6\n"

    add_line("This workbook is documented using auto-generated captions.",
             bold="true", fontsize="18", fontcolor="#000000")
    add_line("")
    add_line("To view the documentation for any sheet:", fontsize="14", fontcolor="#333333")
    add_line("")
    add_line("1. Navigate to the sheet you want to inspect", fontsize="14", fontcolor="#555555")
    add_line("2. Go to Worksheet menu > Show Caption (shortcut: Alt, W, A, Enter)",
             fontsize="14", fontcolor="#555555")
    add_line("3. The caption panel appears at the bottom with full documentation",
             fontsize="14", fontcolor="#555555")
    add_line("")
    add_line("Each caption includes:", fontsize="14", fontcolor="#333333")
    add_line("Purpose, Filters, Hidden Data, Columns, Rows, Marks,", fontsize="12", fontcolor="#555555")
    add_line("Tooltip Details, Table Calc Configuration, Style/Formatting,",
             fontsize="12", fontcolor="#555555")
    add_line("and Dashboard Actions.", fontsize="12", fontcolor="#555555")
    add_line("")
    add_line("A future developer should be able to recreate any sheet from its caption",
             fontsize="14", fontcolor="#027b8e")
    add_line("or identify if changes have been made by comparing it to the caption.",
             fontsize="14", fontcolor="#027b8e")
    add_line("")
    add_line("Documentation generated by GitHub Copilot.",
             italic="true", fontsize="11", fontcolor="#787878")

    # Mark labels style — REQUIRED to show the text (do NOT use show-labels attr)
    pane_style = ET.SubElement(pane, "style")
    pane_sr = ET.SubElement(pane_style, "style-rule", {"element": "mark"})
    ET.SubElement(pane_sr, "format", {"attr": "mark-labels-show", "value": "true"})
    ET.SubElement(pane_sr, "format", {"attr": "mark-labels-cull", "value": "true"})

    ET.SubElement(table, "rows")
    ET.SubElement(table, "cols")
    ET.SubElement(ws, "simple-id", {"uuid": "{" + str(uuid.uuid4()).upper() + "}"})
    print("  Developer Notes sheet added")


# ── Main ──

def main():
    print(f"Reading: {IN_TWB}")
    tree = ET.parse(IN_TWB)
    root = tree.getroot()

    fm = build_field_map(root)
    formula_map = build_formula_map(root)
    action_idx = build_action_index(root, fm)
    parent_map = {c: p for p in root.iter() for c in p}
    print(f"Fields: {len(fm)}, Formulas: {len(formula_map)}, Actions on sheets: {len(action_idx)}")

    worksheets = root.findall(".//worksheet")
    print(f"Worksheets: {len(worksheets)}")

    injected = skipped = 0
    for ws in worksheets:
        wsn = ws.get("name", "")
        if ws.find("layout-options/caption") is not None:
            skipped += 1; continue
        sections = gen_sections(ws, fm, formula_map, action_idx, parent_map)
        cap = build_caption(sections)
        lo = ws.find("layout-options")
        if lo is None:
            lo = ET.Element("layout-options")
            ws.insert(0, lo)
        lo.append(cap)
        injected += 1
        print(f"  {wsn} -> {dashboard_of(wsn)}")

    # Add Developer Notes sheet
    add_developer_notes(root, DS_NAME)

    print(f"\n{injected} injected, {skipped} skipped")
    tree.write(OUT, encoding="utf-8", xml_declaration=True)

    # Post-write fixes
    with open(OUT, "r", encoding="utf-8") as f: c = f.read()
    c = c.replace("<?xml version='1.0' encoding='utf-8'?>",
                  "<?xml version='1.0' encoding='utf-8' ?>")
    c = c.replace("ns0:", "user:").replace("xmlns:ns0=", "xmlns:user=")
    with open(OUT, "w", encoding="utf-8") as f: f.write(c)

    # Repackage as .twbx (optional — remove if not needed)
    if os.path.exists(IMG):
        with zipfile.ZipFile(OUT_X, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(OUT, os.path.basename(OUT))
            zf.write(IMG, "Image/" + os.path.basename(IMG))
        print(f"Packaged: {OUT_X}")

    print(f"Written: {OUT}")


if __name__ == "__main__":
    main()
