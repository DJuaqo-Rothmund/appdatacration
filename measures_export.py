"""
measures_export.py
==================
Exporta las mediciones personalizadas a Excel (.xlsx) sin librerías externas.

El .xlsx es un ZIP con XML (SpreadsheetML). Se escribe lo mínimo que Excel,
Google Sheets y LibreOffice abren sin quejas: textos en línea, números como
números (acepta coma decimal: «23,5» -> 23.5), fila de encabezado en negrita,
columnas con ancho cómodo y el encabezado fijo al desplazarse.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import zipfile
from xml.sax.saxutils import escape

import phenology as ph
from platform_utils import data_subdir, slugify

_NUM = re.compile(r"^[+-]?(\d+([.,]\d*)?|[.,]\d+)$")
_BAD_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def as_number(value):
    """«23,5» -> 23.5 · «12» -> 12 · cualquier otra cosa -> None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    text = str(value or "").strip()
    if not _NUM.match(text):
        return None
    n = float(text.replace(",", "."))
    return int(n) if n.is_integer() and "." not in text and "," not in text else n


def _col(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


def _cell(ref: str, value, style: int = 0) -> str:
    s = f' s="{style}"' if style else ""
    n = as_number(value)
    if n is not None:
        return f'<c r="{ref}"{s}><v>{n}</v></c>'
    text = escape(_BAD_XML.sub("", str(value if value is not None else "")))
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(rows: list[list]) -> str:
    widths = {}
    for row in rows:
        for j, v in enumerate(row):
            widths[j] = min(60, max(widths.get(j, 8), len(str(v if v is not None else "")) + 2))
    cols = "".join(f'<col min="{j + 1}" max="{j + 1}" width="{w}" customWidth="1"/>'
                   for j, w in sorted(widths.items()))
    body = []
    for i, row in enumerate(rows):
        cells = "".join(_cell(f"{_col(j)}{i + 1}", v, 1 if i == 0 else 0) for j, v in enumerate(row))
        body.append(f'<row r="{i + 1}">{cells}</row>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
            f'{"<cols>" + cols + "</cols>" if cols else ""}'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')


def write_xlsx(path: str, sheets: list[tuple[str, list[list]]]) -> str:
    """sheets: [(nombre_hoja, filas)] — la primera fila de cada hoja es el encabezado."""
    names, used = [], set()
    for name, _rows in sheets:
        base = re.sub(r"[\[\]:*?/\\]", " ", name).strip()[:31] or "Hoja"
        n, cand = 1, base
        while cand.lower() in used:
            n += 1
            cand = f"{base[:28]} {n}"
        used.add(cand.lower())
        names.append(cand)
    ct = "".join(f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" ContentType="application/'
                 f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                 for i in range(len(sheets)))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                    '<Default Extension="xml" ContentType="application/xml"/>'
                    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                    f'{ct}</Types>')
        zf.writestr("_rels/.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                    '</Relationships>')
        zf.writestr("xl/workbook.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                    + "".join(f'<sheet name="{escape(n)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                              for i, n in enumerate(names))
                    + '</sheets></workbook>')
        zf.writestr("xl/_rels/workbook.xml.rels",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    + "".join(f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/'
                              f'officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                              for i in range(len(sheets)))
                    + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/'
                      'officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>')
        zf.writestr("xl/styles.xml",
                    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    '<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font>'
                    '<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
                    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                    '<fill><patternFill patternType="gray125"/></fill></fills>'
                    '<borders count="1"><border/></borders>'
                    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
                    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/></cellXfs>'
                    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
                    '</styleSheet>')
        for i, (_name, rows) in enumerate(sheets):
            zf.writestr(f"xl/worksheets/sheet{i + 1}.xml", _sheet_xml(rows))
    return path


def measure_rows(db, measure: dict, season: int | None = None) -> list[list]:
    """Filas (con encabezado) de una medición para toda la temporada."""
    head = ["Semana del año", "Año", "Inicio de semana", "Variedad"]
    if measure["kind"] == "table":
        head += measure["columns"]
        # Columnas que ya no están en la definición, pero tienen datos: no se pierden.
        extra = []
        entries = [e for e in db.list_entries(measure["id"], season=season)
                   if any(str(v).strip() for v in e["data"].values())]   # sin filas vacías
        for e in entries:
            for k in e["data"]:
                if k not in measure["columns"] and k not in extra:
                    extra.append(k)
        head += extra
    else:
        entries = db.list_entries(measure["id"], season=season)
        head += ["Descripción", "Archivo"]
    head.append("Registrado")
    rows = [head]
    for e in entries:
        week, year = ph.iso_week(e["start_date"])
        start = _dt.date.fromisoformat(e["start_date"][:10])
        row = [week, year, f"{start:%d-%m-%Y}", e["variety_name"] or "General"]
        if measure["kind"] == "table":
            row += [e["data"].get(c, "") for c in head[4:-1]]
        else:
            row += [e["caption"] or "", os.path.basename(e["path"] or "")]
        row.append(e["created_at"].replace("T", " ")[:16])
        rows.append(row)
    return rows


def export_measures(db, measure_ids: list[int], season: int | None = None,
                    dest_dir: str | None = None) -> str:
    """Un .xlsx con una hoja por medición. Devuelve la ruta."""
    measures = [m for m in (db.get_measure(i) for i in measure_ids) if m]
    if not measures:
        raise ValueError("No hay mediciones para exportar.")
    dest_dir = dest_dir or data_subdir("reports")
    stem = slugify(measures[0]["name"]) if len(measures) == 1 else "mediciones"
    path = os.path.join(dest_dir, f"{stem}_{season or 'todas'}_{_dt.datetime.now():%Y%m%d_%H%M}.xlsx")
    write_xlsx(path, [(m["name"], measure_rows(db, m, season)) for m in measures])
    db.log("export", "measures", None, os.path.basename(path))
    return path
