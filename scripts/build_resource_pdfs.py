#!/usr/bin/env python3
"""Render the markdown documentation into the downloadable PDFs.

The application's Resources page serves finished PDFs, and the deployed
container is Python-only at runtime, so the PDFs are generated here, committed
to ``public/downloads/`` and treated as build-time assets.

    python3 scripts/build_resource_pdfs.py

Sources live in ``docs/tutorial/*.md``. Output goes to
``public/downloads/*.pdf``. Interface illustrations come from
``scripts/pdf_figures.py``; run ``scripts/check_figures.py`` to verify their
geometry.
"""

from __future__ import annotations

import html
import os
import re
import sys
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import (
        BaseDocTemplate,
        Flowable,
        Frame,
        NextPageTemplate,
        PageBreak,
        PageTemplate,
        Paragraph,
        Preformatted,
        Spacer,
        Table,
        TableStyle,
    )
    from reportlab.platypus.tableofcontents import TableOfContents
except ImportError:  # pragma: no cover
    sys.exit("reportlab is required: pip install reportlab")

import pdf_figures as figs  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_DIR = os.path.join(ROOT, "docs", "tutorial")
OUTPUT_DIR = os.path.join(ROOT, "public", "downloads")

INK = figs.INK
MUTED = figs.MUTED
SUBTLE = figs.SUBTLE
LINE = figs.LINE
ACCENT = figs.ACCENT
ACCENT_LINE = figs.ACCENT_LINE
PAPER = figs.PAPER

BODY_FONT = "Helvetica"
MONO_FONT = "Courier"
MONO_SIZE = 7.4

FIGURES = figs.FIGURES
FIGURE_SIZE = figs.FIGURE_SIZE


def register_fonts() -> None:
    """Prefer DejaVu (full Unicode, so arrows and dashes render) when present."""
    global BODY_FONT, MONO_FONT
    candidates = []
    try:
        import matplotlib

        candidates.append(
            os.path.join(
                os.path.dirname(matplotlib.__file__), "mpl-data", "fonts", "ttf"
            )
        )
    except ImportError:
        pass
    candidates += [
        "/usr/share/fonts/truetype/dejavu",
        "/usr/share/fonts/dejavu",
        "/Library/Fonts",
    ]

    wanted = {
        "Body": "DejaVuSans.ttf",
        "Body-Bold": "DejaVuSans-Bold.ttf",
        "Body-Italic": "DejaVuSans-Oblique.ttf",
        "Body-BoldItalic": "DejaVuSans-BoldOblique.ttf",
        "Mono": "DejaVuSansMono.ttf",
        "Mono-Bold": "DejaVuSansMono-Bold.ttf",
    }
    found = {}
    for directory in candidates:
        if not os.path.isdir(directory):
            continue
        for name, filename in wanted.items():
            if name not in found and os.path.exists(os.path.join(directory, filename)):
                found[name] = os.path.join(directory, filename)
        if len(found) == len(wanted):
            break

    if len(found) != len(wanted):
        print("! DejaVu not found — falling back to Helvetica (limited Unicode)")
        return

    for name, path in found.items():
        pdfmetrics.registerFont(TTFont(name, path))
    pdfmetrics.registerFontFamily(
        "Body",
        normal="Body",
        bold="Body-Bold",
        italic="Body-Italic",
        boldItalic="Body-BoldItalic",
    )
    pdfmetrics.registerFontFamily(
        "Mono", normal="Mono", bold="Mono-Bold", italic="Mono", boldItalic="Mono-Bold"
    )
    BODY_FONT = "Body"
    MONO_FONT = "Mono"
    figs.BODY_FONT = "Body"
    figs.MONO_FONT = "Mono"


# ═════════════════════════════════════════════════════════════════════════
# Markdown → blocks
# ═════════════════════════════════════════════════════════════════════════

@dataclass
class Block:
    kind: str
    lines: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def parse_markdown(text: str) -> list[Block]:
    blocks: list[Block] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        if not stripped or stripped == "---":
            i += 1
            continue

        if stripped.startswith("```"):
            i += 1
            code: list[str] = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1
            blocks.append(Block("code", code))
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            blocks.append(
                Block(f"h{min(len(heading.group(1)), 3)}", [heading.group(2).strip()])
            )
            i += 1
            continue

        figure = re.match(r"^!\[(.*?)\]\(fig:([A-Za-z0-9_-]+)\)\s*$", stripped)
        if figure:
            blocks.append(
                Block("figure", [figure.group(2)], {"caption": figure.group(1)})
            )
            i += 1
            continue

        if stripped.startswith("|"):
            rows: list[str] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            parsed = []
            for row in rows:
                cells = [c.strip() for c in row.strip("|").split("|")]
                if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                    continue  # separator row
                parsed.append(cells)
            if parsed:
                blocks.append(Block("table", parsed))
            continue

        if stripped.startswith(">"):
            quote: list[str] = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(Block("quote", quote))
            continue

        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while i < len(lines):
                m = re.match(r"^[-*]\s+(.*)$", lines[i].strip())
                if not m:
                    if lines[i].startswith("  ") and lines[i].strip() and items:
                        items[-1] += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append(m.group(1))
                i += 1
            blocks.append(Block("ul", items))
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines):
                m = re.match(r"^\d+\.\s+(.*)$", lines[i].strip())
                if not m:
                    break
                items.append(m.group(1))
                i += 1
            blocks.append(Block("ol", items))
            continue

        paragraph: list[str] = []
        while i < len(lines):
            nxt = lines[i].strip()
            if (
                not nxt
                or nxt.startswith(("#", "|", ">", "```", "!["))
                or re.match(r"^[-*]\s+", nxt)
                or re.match(r"^\d+\.\s+", nxt)
                or nxt == "---"
            ):
                break
            paragraph.append(nxt)
            i += 1
        if paragraph:
            blocks.append(Block("p", [" ".join(paragraph)]))
    return blocks


def inline(text: str) -> str:
    """Markdown inline → ReportLab paragraph markup."""
    out = html.escape(text)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(
        r"`(.+?)`", r'<font face="%s" size="%s">\1</font>' % (MONO_FONT, MONO_SIZE), out
    )
    out = re.sub(r"(?<![\w*])\*([^*]+)\*(?![\w*])", r"<i>\1</i>", out)
    out = re.sub(
        r"\[(.+?)\]\((.+?)\)",
        r'<link href="\2" color="#0D748A"><u>\1</u></link>',
        out,
    )
    return out


# ═════════════════════════════════════════════════════════════════════════
# Flowables
# ═════════════════════════════════════════════════════════════════════════

class Figure(Flowable):
    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.width, self.height = FIGURE_SIZE.get(name, (460, 120))

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        FIGURES[self.name](self.canv, 0, 0, self.width, self.height)


class Caption(Flowable):
    def __init__(self, text: str, width: float):
        super().__init__()
        self.caption = text
        self.width = width
        self.height = 18

    def wrap(self, availWidth, availHeight):
        return self.width, self.height

    def draw(self):
        c = self.canv
        c.saveState()
        c.setFont(BODY_FONT, 7.2)
        c.setFillColor(MUTED)
        line = self.caption if len(self.caption) <= 155 else self.caption[:152] + "…"
        c.drawString(0, self.height - 8, line)
        c.restoreState()


# ═════════════════════════════════════════════════════════════════════════
# Styles
# ═════════════════════════════════════════════════════════════════════════

def build_styles():
    base = getSampleStyleSheet()
    s = {}
    s["cover-title"] = ParagraphStyle(
        "cover-title", parent=base["Title"], fontName=BODY_FONT, fontSize=25,
        leading=30, textColor=INK, alignment=TA_LEFT, spaceAfter=6,
    )
    s["cover-sub"] = ParagraphStyle(
        "cover-sub", parent=base["Normal"], fontName=BODY_FONT, fontSize=11,
        leading=16, textColor=MUTED, alignment=TA_LEFT,
    )
    s["cover-meta"] = ParagraphStyle(
        "cover-meta", parent=base["Normal"], fontName=BODY_FONT, fontSize=8.4,
        leading=13, textColor=SUBTLE,
    )
    s["h1"] = ParagraphStyle(
        "h1", parent=base["Heading1"], fontName=BODY_FONT, fontSize=15.5,
        leading=19, textColor=ACCENT, spaceBefore=16, spaceAfter=7,
    )
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName=BODY_FONT, fontSize=11.2,
        leading=14, textColor=INK, spaceBefore=11, spaceAfter=4,
    )
    s["h3"] = ParagraphStyle(
        "h3", parent=base["Heading3"], fontName=BODY_FONT, fontSize=9.4,
        leading=12, textColor=MUTED, spaceBefore=8, spaceAfter=3,
    )
    s["p"] = ParagraphStyle(
        "p", parent=base["Normal"], fontName=BODY_FONT, fontSize=9,
        leading=13.4, textColor=INK, spaceAfter=6, alignment=TA_LEFT,
    )
    s["li"] = ParagraphStyle(
        "li", parent=s["p"], spaceAfter=2.5, leftIndent=12, bulletIndent=2
    )
    s["cell"] = ParagraphStyle(
        "cell", parent=base["Normal"], fontName=BODY_FONT, fontSize=7.5,
        leading=10, textColor=INK,
    )
    s["tablehead"] = ParagraphStyle(
        "tablehead", parent=base["Normal"], fontName=BODY_FONT, fontSize=7.6,
        leading=10, textColor=ACCENT,
    )
    s["quote"] = ParagraphStyle("quote", parent=s["p"], textColor=MUTED, leftIndent=10)
    s["tocheading"] = ParagraphStyle(
        "tocheading", parent=s["h1"], spaceBefore=0
    )
    s["toc1"] = ParagraphStyle(
        "toc1", parent=base["Normal"], fontName=BODY_FONT, fontSize=10,
        leading=17, textColor=INK,
    )
    s["toc2"] = ParagraphStyle(
        "toc2", parent=base["Normal"], fontName=BODY_FONT, fontSize=8.8,
        leading=13.5, textColor=MUTED, leftIndent=14,
    )
    s["codebox"] = ParagraphStyle(
        "codebox", fontName=MONO_FONT, fontSize=7.4, leading=10.4, textColor=INK
    )
    return s


# ═════════════════════════════════════════════════════════════════════════
# Renderers
# ═════════════════════════════════════════════════════════════════════════

PAGE_W, PAGE_H = A4
MARGIN_X = 20 * mm
MARGIN_TOP = 18 * mm
MARGIN_BOTTOM = 16 * mm
CONTENT_W = PAGE_W - 2 * MARGIN_X


class Manual(BaseDocTemplate):
    def __init__(self, filename, title, **kwargs):
        super().__init__(
            filename,
            pagesize=A4,
            leftMargin=MARGIN_X,
            rightMargin=MARGIN_X,
            topMargin=MARGIN_TOP,
            bottomMargin=MARGIN_BOTTOM,
            title=title,
            author="Chemical Data Extractor",
            subject=title,
            **kwargs,
        )
        self.doc_title = title
        frame = Frame(
            MARGIN_X, MARGIN_BOTTOM, CONTENT_W,
            PAGE_H - MARGIN_TOP - MARGIN_BOTTOM, id="body",
        )
        self.addPageTemplates(
            [
                PageTemplate(id="cover", frames=[frame], onPage=self._cover_page),
                PageTemplate(id="body", frames=[frame], onPage=self._body_page),
            ]
        )

    def _cover_page(self, canv, doc):
        canv.saveState()
        canv.setFillColor(ACCENT)
        canv.rect(0, PAGE_H - 6, PAGE_W, 6, stroke=0, fill=1)
        canv.setFillColor(figs.SURFACE_3)
        canv.rect(0, 0, PAGE_W, 5, stroke=0, fill=1)
        canv.restoreState()

    def _body_page(self, canv, doc):
        canv.saveState()
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.5)
        canv.line(MARGIN_X, PAGE_H - MARGIN_TOP + 8, PAGE_W - MARGIN_X, PAGE_H - MARGIN_TOP + 8)
        canv.setFont(BODY_FONT, 7)
        canv.setFillColor(SUBTLE)
        canv.drawString(MARGIN_X, PAGE_H - MARGIN_TOP + 12, self.doc_title)
        canv.drawRightString(
            PAGE_W - MARGIN_X, MARGIN_BOTTOM - 10,
            f"Chemical Data Extractor · page {doc.page}",
        )
        canv.restoreState()

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph):
            name = flowable.style.name
            if name in ("h1", "h2"):
                level = 0 if name == "h1" else 1
                self.notify("TOCEntry", (level, flowable.getPlainText(), self.page))


def column_widths(rows, available):
    cols = max(len(r) for r in rows)
    weights = []
    for idx in range(cols):
        longest = 0
        for row in rows:
            if idx < len(row):
                longest = max(longest, len(row[idx]))
        weights.append(min(max(longest, 6), 46))
    total = sum(weights)
    widths = [available * w / total for w in weights]
    if cols == 2:
        # A two-column table is label-then-prose; the label stays narrow so the
        # explanation is not squeezed into a column.
        widths[0] = min(widths[0], available * 0.33)
        widths[1] = available - widths[0]
    return widths


def render_table(rows, styles, available):
    data = []
    for r_i, row in enumerate(rows):
        style = styles["tablehead"] if r_i == 0 else styles["cell"]
        data.append([Paragraph(inline(c), style) for c in row])
    table = Table(data, colWidths=column_widths(rows, available), hAlign="LEFT")
    commands = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, ACCENT_LINE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, LINE),
    ]
    for i in range(1, len(data)):
        if i % 2 == 1:
            commands.append(("BACKGROUND", (0, i), (-1, i), figs.SURFACE_2))
    table.setStyle(TableStyle(commands))
    return table


def render_quote(lines, styles, available):
    inner = Paragraph(inline(" ".join(lines)), styles["quote"])
    table = Table([[inner]], colWidths=[available], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F4F8F9")),
                ("LINEBEFORE", (0, 0), (0, -1), 2, ACCENT),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    return table


def render_code(lines, styles, available):
    body = "\n".join(lines) if lines else " "
    width = min(
        available,
        max([pdfmetrics.stringWidth(l, MONO_FONT, 7.4) + 18 for l in lines] or [200]),
    )
    pre = Preformatted(body, styles["codebox"])
    table = Table([[pre]], colWidths=[width], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F7F9")),
                ("BOX", (0, 0), (-1, -1), 0.4, LINE),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    return table


# ═════════════════════════════════════════════════════════════════════════
# Build
# ═════════════════════════════════════════════════════════════════════════

def build(markdown_path: str, pdf_path: str, title: str, subtitle: str, meta):
    with open(markdown_path, encoding="utf-8") as fh:
        source = fh.read()
    blocks = parse_markdown(source)
    styles = build_styles()
    story = []

    # ── Cover ──────────────────────────────────────────────────────────────
    story.append(Spacer(1, 44 * mm))
    story.append(Paragraph("Chemical Data Extractor", styles["cover-meta"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(title, styles["cover-title"]))
    story.append(Paragraph(subtitle, styles["cover-sub"]))
    story.append(Spacer(1, 10))
    rule = Table([[""]], colWidths=[54], rowHeights=[2.2], hAlign="LEFT")
    rule.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
    story.append(rule)
    story.append(Spacer(1, 14))
    for line in meta:
        story.append(Paragraph(line, styles["cover-meta"]))
    story.append(Spacer(1, 22 * mm))
    story.append(
        Paragraph(
            "Generated from the application's documentation source "
            "(<font face='%s' size='7'>docs/tutorial/%s</font>), so it always "
            "matches the shipped interface."
            % (MONO_FONT, os.path.basename(markdown_path)),
            styles["cover-meta"],
        )
    )
    story.append(NextPageTemplate("body"))
    story.append(PageBreak())

    # ── Contents ───────────────────────────────────────────────────────────
    story.append(Paragraph("Contents", styles["tocheading"]))
    toc = TableOfContents()
    toc.levelStyles = [styles["toc1"], styles["toc2"]]
    toc.dotsMinLevel = 0
    story.append(toc)
    story.append(PageBreak())

    # ── Body ───────────────────────────────────────────────────────────────
    for block in blocks:
        kind = block.kind
        if kind in ("h1", "h2", "h3"):
            story.append(Paragraph(inline(block.lines[0]), styles[kind]))
        elif kind == "p":
            story.append(Paragraph(inline(block.lines[0]), styles["p"]))
        elif kind in ("ul", "ol"):
            for idx, item in enumerate(block.lines):
                bullet = "•" if kind == "ul" else f"{idx + 1}."
                story.append(Paragraph(inline(item), styles["li"], bulletText=bullet))
            story.append(Spacer(1, 4))
        elif kind == "quote":
            story.append(render_quote(block.lines, styles, CONTENT_W))
            story.append(Spacer(1, 7))
        elif kind == "code":
            story.append(render_code(block.lines, styles, CONTENT_W))
            story.append(Spacer(1, 7))
        elif kind == "table":
            story.append(render_table(block.lines, styles, CONTENT_W))
            story.append(Spacer(1, 8))
        elif kind == "figure":
            name = block.lines[0]
            if name not in FIGURES:
                print(f"! unknown figure: {name}")
                continue
            story.append(Figure(name))
            caption = block.meta.get("caption", "")
            if caption:
                story.append(Caption(caption, CONTENT_W))
            story.append(Spacer(1, 10))

    doc = Manual(pdf_path, title)
    doc.multiBuild(story)
    return doc.page


JOBS = [
    {
        "source": "user-tutorial.md",
        "output": "Chemical-Data-Extractor-User-Tutorial.pdf",
        "title": "User Tutorial",
        "subtitle": "From your first search to advanced, repeatable workflows",
        "meta": [
            "Version 3.2 &nbsp;·&nbsp; Complete guide, beginner to advanced",
            "19 database connectors &nbsp;·&nbsp; multi-hop graph traversal &nbsp;·&nbsp; enrichment pipeline",
            "",
            "Covers: concepts, accounts, running a search, reading a run, export files,",
            "search history, the AI analyst, plans and payment, light and dark mode,",
            "troubleshooting, and batch and command-line use.",
        ],
    },
    {
        "source": "quick-reference.md",
        "output": "Chemical-Data-Extractor-Quick-Reference.pdf",
        "title": "Quick Reference",
        "subtitle": "Everything you need while running a search, on two pages",
        "meta": [
            "Version 3.2 &nbsp;·&nbsp; Desk card",
            "",
            "Print this one. It contains the search steps, expansion depths, status",
            "meanings, the export-file map, plan limits and the short troubleshooting table.",
        ],
    },
    {
        "source": "export-formats.md",
        "output": "Chemical-Data-Extractor-Export-Formats-Reference.pdf",
        "title": "Export Formats Reference",
        "subtitle": "What each downloaded file contains, and which tool opens it",
        "meta": [
            "Version 3.2 &nbsp;·&nbsp; Data dictionary",
            "",
            "Column-level detail for nodes.csv, edges.csv, the GraphML, Cypher, RDF, Parquet",
            "and JSON exports, plus guidance on choosing the right file for the job.",
        ],
    },
]


def main() -> int:
    register_fonts()
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for job in JOBS:
        source = os.path.join(SOURCE_DIR, job["source"])
        output = os.path.join(OUTPUT_DIR, job["output"])
        if not os.path.exists(source):
            print(f"! missing source: {source}")
            return 1
        pages = build(source, output, job["title"], job["subtitle"], job["meta"])
        size_kb = os.path.getsize(output) / 1024
        print(f"✓ {job['output']} — {pages} pages, {size_kb:.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
