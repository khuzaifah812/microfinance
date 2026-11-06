import csv
import io
from datetime import date, datetime
from decimal import Decimal

from django.http import HttpResponse
from django.utils import timezone

from core.utils import fmt_money


def fmt(v, typ):
    if v is None or v == "":
        return ""
    if typ == "money":
        return fmt_money(v) if isinstance(v, (int, float, Decimal)) else str(v)
    if typ == "int":
        return f"{int(v):,}" if isinstance(v, (int, float, Decimal)) else str(v)
    if typ == "date" and isinstance(v, (date, datetime)):
        return v.strftime("%d %b %Y")
    return str(v)


def table_rows(data):
    cols = data["columns"]
    out = [[fmt(v, cols[i][1]) for i, v in enumerate(r)] for r in data["rows"]]
    tot = [fmt(v, cols[i][1]) for i, v in enumerate(data["totals"])] if data.get("totals") else None
    return out, tot


def to_csv(title, data, filename):
    resp = HttpResponse(content_type="text/csv; charset=utf-8")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.csv"'
    resp.write("﻿")
    w = csv.writer(resp)
    w.writerow([title])
    w.writerow([c[0] for c in data["columns"]])
    for r in data["rows"]:
        w.writerow([str(v) if isinstance(v, Decimal) else (v.isoformat() if isinstance(v, (date, datetime)) else v) for v in r])
    if data.get("totals"):
        w.writerow([str(v) if isinstance(v, Decimal) else v for v in data["totals"]])
    return resp


def to_xlsx(title, data, filename, org):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws = wb.active
    ws.title = title[:30]
    ws.append([org])
    ws.append([title])
    ws.append([f"Generated {timezone.localtime():%d %b %Y %H:%M}"])
    ws.append([])
    ws["A1"].font, ws["A2"].font = Font(bold=True, size=14, color="14532D"), Font(bold=True, size=12)
    ws.append([c[0] for c in data["columns"]])
    hdr = ws.max_row
    for cell in ws[hdr]:
        cell.font, cell.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="14532D")
    types = [c[1] for c in data["columns"]]

    def conv(v, t):
        if isinstance(v, Decimal):
            return int(v) if t in ("money", "int") and v == v.to_integral_value() else float(v)
        return v

    for r in data["rows"]:
        ws.append([conv(v, types[i]) for i, v in enumerate(r)])
    if data.get("totals"):
        ws.append([conv(v, types[i]) for i, v in enumerate(data["totals"])])
        for cell in ws[ws.max_row]:
            cell.font = Font(bold=True)
    for row in ws.iter_rows(min_row=hdr + 1):
        for i, cell in enumerate(row):
            if types[i] == "money":
                cell.number_format, cell.alignment = "#,##0;[Red](#,##0)", Alignment(horizontal="right")
            elif types[i] == "date":
                cell.number_format = "dd mmm yyyy"
    for i, c in enumerate(data["columns"]):
        width = max([len(str(c[0]))] + [len(fmt(r[i], c[1])) for r in data["rows"][:300]]) + 2
        ws.column_dimensions[chr(65 + i) if i < 26 else "A"].width = min(width, 60)
    ws.freeze_panes = ws.cell(row=hdr + 1, column=1)
    buf = io.BytesIO()
    wb.save(buf)
    resp = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.xlsx"'
    return resp


def to_pdf(title, data, filename, org, subtitle=""):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    cols = data["columns"]
    wide = len(cols) > 6
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4) if wide else A4, leftMargin=12 * mm, rightMargin=12 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=title)
    ss = getSampleStyleSheet()
    green = colors.HexColor("#14532D")
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=green, fontSize=15, alignment=0, spaceAfter=2)
    small = ParagraphStyle("sm", parent=ss["Normal"], fontSize=8, textColor=colors.grey)
    cell = ParagraphStyle("cell", parent=ss["Normal"], fontSize=7.5, leading=9)
    cellr = ParagraphStyle("cellr", parent=cell, alignment=2)
    rows, tot = table_rows(data)

    def P(v, i):
        return Paragraph(str(v).replace("&", "&amp;").replace("<", "&lt;"), cellr if cols[i][1] in ("money", "int") else cell)

    body = [[Paragraph(f"<b>{c[0]}</b>", ParagraphStyle("h", parent=cell, textColor=colors.white, alignment=2 if c[1] in ("money", "int") else 0)) for c in cols]]
    body += [[P(v, i) for i, v in enumerate(r)] for r in rows]
    if tot:
        body.append([Paragraph(f"<b>{str(v)}</b>", cellr if cols[i][1] in ("money", "int") else cell) for i, v in enumerate(tot)])
    t = Table(body, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), green), ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E0DB")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F3F5F2")])]
    if tot:
        style.append(("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#E6EFE8")))
    t.setStyle(TableStyle(style))

    def footer(canv, d):
        canv.saveState()
        canv.setFont("Helvetica", 7)
        canv.setFillColor(colors.grey)
        canv.drawString(12 * mm, 8 * mm, f"{org} - {title} - generated {timezone.localtime():%d %b %Y %H:%M}")
        canv.drawRightString(d.pagesize[0] - 12 * mm, 8 * mm, f"Page {d.page}")
        canv.restoreState()

    story = [Paragraph(org, small), Paragraph(title, h1), Paragraph(subtitle, small), Spacer(1, 4 * mm), t]
    if data.get("notes"):
        story += [Spacer(1, 3 * mm), Paragraph(data["notes"], small)]
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
    resp["Content-Disposition"] = f'attachment; filename="{filename}.pdf"'
    return resp
