"""Vector recreations of the application interface for the documentation PDFs.

These are drawings, not screenshots: every figure is composed from the same
wording and layout as the live interface, so it stays sharp at any zoom and
needs no image assets. ``scripts/check_figures.py`` verifies that nothing is
drawn outside its frame and that no two text labels collide.

Coordinate system: origin top-left, y grows downward (as in CSS), which is what
the layout numbers below are written in.
"""

from __future__ import annotations

from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics

INK = colors.HexColor("#111820")
MUTED = colors.HexColor("#525E6C")
SUBTLE = colors.HexColor("#6C7886")
LINE = colors.HexColor("#DFE4EA")
LINE_STRONG = colors.HexColor("#C7CFD9")
SURFACE = colors.HexColor("#F7F9FB")
SURFACE_2 = colors.HexColor("#FAFBFC")
SURFACE_3 = colors.HexColor("#EEF2F6")
ACCENT = colors.HexColor("#0D748A")
ACCENT_SOFT = colors.HexColor("#E0F3F6")
ACCENT_LINE = colors.HexColor("#A3D6E0")
GOOD = colors.HexColor("#046C4E")
WARN = colors.HexColor("#925808")
BAD = colors.HexColor("#B91C1C")
PAPER = colors.white

BODY_FONT = "Helvetica"
MONO_FONT = "Courier"

# Palette for the status pills, mirroring the application's status tones.
PILL_TONES = {
    "accent": (ACCENT_SOFT, ACCENT_LINE, ACCENT),
    "good": (colors.HexColor("#ECF9F3"), colors.HexColor("#ADE2CD"), GOOD),
    "warn": (colors.HexColor("#FEF7E8"), colors.HexColor("#F0D69E"), WARN),
    "bad": (colors.HexColor("#FEF2F2"), colors.HexColor("#FECACA"), BAD),
    "neutral": (SURFACE_3, LINE, MUTED),
}


class Wire:
    """A small drawing toolkit over a ReportLab canvas."""

    def __init__(self, c, width, height, body_font=None, mono_font=None):
        self.c = c
        self.w = width
        self.h = height
        self.bottom = height
        # Resolved at construction so a later font registration still applies.
        self.body = body_font or BODY_FONT
        self.mono = mono_font or MONO_FONT

    def y(self, y):
        """Convert a top-down y to canvas coordinates."""
        return self.bottom - y

    # ── primitives ─────────────────────────────────────────────────────────
    def box(self, x, y, w, h, fill=None, stroke=LINE, radius=4, lw=0.6):
        c = self.c
        c.saveState()
        c.setLineWidth(lw)
        if stroke is not None:
            c.setStrokeColor(stroke)
        mode = 0  # fill only
        if fill is None:
            mode = 1  # stroke only
        elif stroke is None:
            mode = 0
        else:
            mode = 0  # reportlab: stroke=1 fill=1 draws both
        if fill is not None:
            c.setFillColor(fill)
            if stroke is not None:
                c.roundRect(
                    x, self.y(y + h), w, h, radius, stroke=1, fill=1
                )
            else:
                c.roundRect(x, self.y(y + h), w, h, radius, stroke=0, fill=1)
        else:
            c.roundRect(x, self.y(y + h), w, h, radius, stroke=1, fill=0)
        c.restoreState()
        _ = mode

    def text(self, x, y, s, size=6.2, color=INK, font=None, align="l"):
        c = self.c
        # Geometry hooks (used by scripts/check_figures.py) see the real font
        # size, which the bare canvas calls below cannot carry.
        if hasattr(c, "note_text"):
            c.note_text(x, y, s, size, font or self.body, align)
        c.saveState()
        c.setFillColor(color)
        c.setFont(font or self.body, size)
        if align == "c":
            c.drawCentredString(x, self.y(y) - size, s)
        elif align == "r":
            c.drawRightString(x, self.y(y) - size, s)
        else:
            c.drawString(x, self.y(y) - size, s)
        c.restoreState()

    def width_of(self, s, size, font=None):
        return pdfmetrics.stringWidth(s, font or self.body, size)

    def label(self, x, y, s, size=5.2, color=SUBTLE):
        self.text(x, y, s.upper(), size=size, color=color)

    def field(self, x, y, w, placeholder, state="empty", h=16):
        fill = SURFACE if state == "focus" else PAPER
        stroke = ACCENT if state == "focus" else LINE_STRONG
        self.box(x, y, w, h, fill=fill, stroke=stroke, radius=3)
        color = MUTED if state != "empty" else SUBTLE
        self.text(x + 6, y + h - 8, placeholder, size=6.0, color=color)

    def button(self, x, y, w, h, text, primary=False, disabled=False, size=6.2):
        if primary:
            fill = LINE_STRONG if disabled else colors.Color(0.047, 0.416, 0.502)
            self.box(x, y, w, h, fill=fill, stroke=None, radius=3)
            self.text(
                x + w / 2,
                y + h / 2 - size / 2 - 1,
                text,
                size=size,
                color=SUBTLE if disabled else PAPER,
                align="c",
            )
        else:
            self.box(x, y, w, h, fill=PAPER, stroke=LINE_STRONG, radius=3)
            self.text(
                x + w / 2,
                y + h / 2 - size / 2 - 1,
                text,
                size=size,
                color=SUBTLE if disabled else MUTED,
                align="c",
            )

    def segmented(self, x, y, w, items, active=0, h=15, gap=2, size=6.0):
        self.box(x, y, w, h, fill=SURFACE, stroke=LINE, radius=4)
        n = len(items)
        cw = (w - 2 * gap - (n - 1) * gap) / n
        for idx, item in enumerate(items):
            cx = x + gap + idx * (cw + gap)
            if idx == active:
                self.box(cx, y + gap, cw, h - 2 * gap, fill=PAPER, stroke=LINE, radius=3)
            self.text(
                cx + cw / 2,
                y + h / 2 - size / 2 - 1,
                item,
                size=size,
                color=INK if idx == active else MUTED,
                align="c",
            )

    def pill(self, x, y, text, tone="accent", h=11, size=5.2, pad=6):
        fill, stroke, ink = PILL_TONES[tone]
        width = self.width_of(text, size) + pad * 2
        self.box(x, y, width, h, fill=fill, stroke=stroke, radius=3)
        self.text(
            x + width / 2, y + h / 2 - size / 2 - 1, text, size=size, color=ink, align="c"
        )
        return width

    def brand(self, x, y, size=6.4):
        self.box(x, y, 14, 14, fill=ACCENT_SOFT, stroke=ACCENT_LINE, radius=3)
        self.text(x + 7, y + 4.6, "G", size=6.4, color=ACCENT, align="c")
        self.text(x + 19, y + 4.4, "Chemical Data Extractor", size=size, color=INK)

    def header(self, x, y, w, items, active=0, with_account=True):
        """The application header: brand, section tabs, theme switch, sign-in."""
        self.brand(x + 10, y + 8)
        right = x + w - 10
        if with_account:
            right -= 36
            self.box(right, y + 8, 36, 13, fill=PAPER, stroke=LINE_STRONG, radius=3)
            self.text(right + 18, y + 13, "Sign in", size=5.4, color=MUTED, align="c")
        right -= 26
        self.box(right, y + 8, 22, 13, fill=SURFACE, stroke=LINE, radius=4)
        self.box(right + 1.5, y + 9.5, 9, 10, fill=PAPER, stroke=LINE, radius=2)
        self.text(right + 6, y + 12, "☼", size=4.6, color=ACCENT, align="c")
        self.text(right + 16, y + 12, "☾", size=4.6, color=SUBTLE, align="c")
        for idx, item in enumerate(items):
            colour = ACCENT if idx == active else MUTED
            # Reserve the label's own width, then draw with its left edge at
            # the new position so the right edge lands exactly 12pt clear of
            # whatever is to its right.
            right -= self.width_of(item, 5.6) + 12
            self.text(right, y + 12.4, item, size=5.6, color=colour)
        return y + 26

    def callout(self, x, y, title, body, w=150):
        """Accent-ruled annotation to the right of a drawing."""
        lines = []
        remaining = body
        while remaining:
            words = remaining.split()
            line = ""
            for word in words:
                candidate = (line + " " + word).strip()
                if self.width_of(candidate, 5.0) > w and line:
                    lines.append(line)
                    line = word
                else:
                    line = candidate
            if line:
                lines.append(line)
            remaining = ""
        total = 8 + len(lines) * 8
        c = self.c
        c.saveState()
        c.setStrokeColor(ACCENT)
        c.setLineWidth(2)
        c.line(x, self.y(y + 4), x, self.y(y + total - 4))
        c.restoreState()
        self.text(x + 6, y, title, size=5.4, color=ACCENT)
        for idx, line in enumerate(lines):
            self.text(x + 6, y + 9 + idx * 8, line, size=5.0, color=MUTED)
        return total


# ═════════════════════════════════════════════════════════════════════════
# Figures
# ═════════════════════════════════════════════════════════════════════════

def fig_searchform(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=PAPER, stroke=LINE, radius=5)
    wire.header(x, y, w, ("Search", "History", "Resources"), active=0)

    wire.text(x + 10, y + 34, "Build a multi-hop knowledge graph", size=7.6, color=INK)
    wire.text(x + 10, y + 44, "from any protein or compound", size=7.6, color=INK)

    top = y + 56
    pw = 190
    ph = h - (top - y) - 10
    wire.box(x + 10, top, pw, ph, fill=SURFACE, stroke=LINE, radius=5)

    ix, iw = x + 18, pw - 16
    wire.label(ix, top + 10, "New search")
    wire.text(ix + iw, top + 10, "Ready", size=5.2, color=SUBTLE, align="r")
    wire.box(ix, top + 18, iw, 16, fill=SURFACE_2, stroke=LINE, radius=3)
    wire.text(ix + 6, top + 23, "How it works, and what a hop is", size=5.2, color=MUTED)
    wire.text(ix + iw - 6, top + 23, "▾", size=5.2, color=SUBTLE, align="r")

    wire.label(ix, top + 42, "Query")
    wire.field(ix, top + 48, iw, "tubulin, Aspirin, EGFR…", state="focus")
    wire.label(ix, top + 70, "Query type")
    wire.segmented(ix, top + 76, iw, ("Auto", "Protein", "Ligand"), active=0)
    wire.label(ix, top + 97, "Expansion depth")
    wire.segmented(ix, top + 103, iw, ("1", "2", "3", "4"), active=0)
    wire.text(ix, top + 124, "Direct relationships only — e.g.", size=5.0, color=SUBTLE)
    wire.text(ix, top + 132, "Aspirin → COX-1. Fastest.", size=5.0, color=SUBTLE)
    wire.button(ix, top + 144, iw, 18, "Run search", primary=True)

    rx = x + 208
    rw = w - rx - 10
    wire.box(rx, top, rw, 84, fill=PAPER, stroke=LINE, radius=5)
    wire.text(rx + 10, top + 10, "Ready when you are", size=6.4, color=INK)
    wire.text(
        rx + 10,
        top + 24,
        "Enter a protein or compound on the left and run a search.",
        size=5.2,
        color=MUTED,
    )
    wire.text(
        rx + 10,
        top + 33,
        "The engine expands it across every connected database,",
        size=5.2,
        color=MUTED,
    )
    wire.text(
        rx + 10,
        top + 42,
        "streams its progress, then lists the files you can download.",
        size=5.2,
        color=MUTED,
    )
    bw = (rw - 20 - 8) / 3
    for idx, (title, detail) in enumerate(
        (
            ("Proteins", "UniProt, PDB"),
            ("Compounds", "PubChem, ChEMBL"),
            ("Pathways", "KEGG, Reactome"),
        )
    ):
        bx = rx + 10 + idx * (bw + 4)
        wire.box(bx, top + 54, bw, 22, fill=SURFACE, stroke=LINE, radius=3)
        wire.text(bx + 6, top + 60, title, size=5.4, color=INK)
        wire.text(bx + 6, top + 68, detail, size=4.8, color=SUBTLE)

    wire.callout(rx, top + 96, "Three controls, one button.", "Only the depth changes the size of the result.", w=rw - 8)


def fig_progress(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=PAPER, stroke=LINE, radius=5)

    wire.pill(x + 10, y + 8, "Completed", tone="good")
    wire.text(x + 58, y + 10, "tubulin  ·  1-hop  ·  33.8s", size=6.2, color=INK)
    wire.text(x + w - 10, y + 10, "a3f19c7e", size=5.2, color=SUBTLE, font=wire.mono, align="r")
    wire.box(x + 10, y + 22, w - 20, 0.6, fill=LINE, stroke=None, radius=0)
    wire.box(x + 10, y + 28, w - 20, 12, fill=SURFACE_2, stroke=None, radius=2)
    wire.text(
        x + 14,
        y + 32,
        "Pipeline finished — 14 export files written",
        size=5.2,
        color=MUTED,
        font=wire.mono,
    )

    wire.text(x + 10, y + 50, "Export files", size=6.4, color=INK)
    wire.text(x + w - 10, y + 50, "14 files · 33.8s", size=5.2, color=SUBTLE, align="r")

    for idx, (name, kind) in enumerate(
        (
            ("tubulin_Knowledge_Graph.xlsx", "Excel workbook"),
            ("nodes.csv", "CSV table"),
            ("graph.graphml", "GraphML graph"),
        )
    ):
        ry = y + 60 + idx * 15
        wire.box(x + 10, ry, w - 20, 14, fill=SURFACE_2 if idx == 0 else PAPER, stroke=LINE, radius=0)
        wire.box(x + 14, ry + 3, 26, 8, fill=PAPER, stroke=LINE, radius=2)
        wire.text(
            x + 27, ry + 4.6, name.rsplit(".", 1)[-1][:4].upper(), size=4, color=MUTED, align="c"
        )
        wire.text(x + 46, ry + 2.4, name, size=5.4, color=INK)
        wire.text(x + 46, ry + 8.2, kind, size=4.6, color=SUBTLE)
        wire.text(x + w - 16, ry + 4, "↓", size=6, color=SUBTLE, align="r")

    ly = y + 112
    lh = h - (ly - y) - 10
    wire.box(x + 10, ly, w - 20, lh, fill=colors.HexColor("#F5F7F9"), stroke=LINE, radius=3)
    wire.label(x + 16, ly + 8, "Engine log")
    wire.text(x + w - 16, ly + 8, "Streaming live", size=4.8, color=SUBTLE, align="r")
    log_lines = [
        (SUBTLE, "╔══════════════════════════════════════╗"),
        (MUTED, "[1/6] Intelligent Routing & Expansion"),
        (GOOD, "[OK] UniProt — 42 entities"),
        (GOOD, "[OK] ChEMBL — 128 bioactivities"),
        (MUTED, "[4/6] Running Graph Analytics Engine"),
        (WARN, "[warn] AlphaFold DB timed out — skipped"),
        (MUTED, "[6/6] Generating multi-format exports"),
        (GOOD, "[OK] Pipeline finished successfully"),
    ]
    for idx, (tone, line) in enumerate(log_lines):
        wire.text(x + 16, ly + 20 + idx * 10, line, size=4.8, color=tone, font=wire.mono)


def fig_exports(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=PAPER, stroke=LINE, radius=5)
    wire.text(x + 10, y + 10, "Export files", size=6.6, color=INK)
    wire.text(x + w - 10, y + 11, "14 files · 33.8s", size=5.2, color=SUBTLE, align="r")

    files = [
        ("xlsx", "tubulin_Knowledge_Graph.xlsx", "Excel workbook · all sheets"),
        ("csv", "nodes.csv", "One row per entity"),
        ("csv", "edges.csv", "One row per relationship"),
        ("cyp", "neo4j_import.cypher", "CREATE statements for Neo4j"),
        ("gra", "graph.graphml", "Whole graph for Gephi or Cytoscape"),
        ("ttl", "graph.ttl", "RDF triples for a linked-data store"),
    ]
    for idx, (ext, name, desc) in enumerate(files):
        ry = y + 22 + idx * 15
        wire.box(x + 8, ry, w - 16, 14, fill=PAPER, stroke=None, radius=0)
        wire.box(x + 12, ry + 3, 22, 8, fill=SURFACE_2, stroke=LINE, radius=2)
        wire.text(x + 23, ry + 4.6, ext, size=3.8, color=MUTED, align="c")
        wire.text(x + 40, ry + 2, name, size=5.4, color=INK)
        wire.text(x + 40, ry + 8.6, desc, size=4.6, color=SUBTLE)
        wire.text(x + w - 16, ry + 4, "↓", size=6, color=SUBTLE, align="r")

    ny = y + h - 40
    wire.box(x + 8, ny, w - 16, 32, fill=SURFACE, stroke=None, radius=3)
    wire.text(x + 14, ny + 8, "Exports are temporary — the server deletes", size=5.0, color=MUTED)
    wire.text(x + 14, ny + 17, "generated files about 30 minutes after the", size=5.0, color=MUTED)
    wire.text(x + 14, ny + 26, "search completes. Download what you keep.", size=5.0, color=MUTED)


def fig_hops(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=SURFACE, stroke=LINE, radius=5)
    wire.text(x + 12, y + 12, "Each hop is one relationship step away from the query.", size=5.6, color=MUTED)

    labels = [
        ("Aspirin", "compound"),
        ("COX-1", "target"),
        ("Prostaglandin pathway", "path"),
        ("Inflammatory disease", "disease"),
    ]
    r = 15
    centres = [x + 40 + idx * ((w - 92) / 3) for idx in range(4)]
    cy = y + 56

    for idx in range(3):
        c.saveState()
        c.setStrokeColor(LINE_STRONG if idx == 0 else ACCENT_LINE)
        c.setLineWidth(1)
        if idx:
            c.setDash(2, 2)
        c.line(centres[idx] + r + 2, wire.y(cy), centres[idx + 1] - r - 2, wire.y(cy))
        c.restoreState()

    tones = {
        "compound": (ACCENT_SOFT, ACCENT_LINE, ACCENT),
        "target": (colors.HexColor("#EAF0FB"), colors.HexColor("#C3D3EC"), colors.HexColor("#2F5C9E")),
        "path": (colors.HexColor("#ECF9F3"), colors.HexColor("#ADE2CD"), GOOD),
        "disease": (colors.HexColor("#FEF7E8"), colors.HexColor("#F0D69E"), WARN),
    }
    for cx, (label, tone) in zip(centres, labels):
        fill, stroke, ink = tones[tone]
        c.saveState()
        c.setFillColor(fill)
        c.setStrokeColor(stroke)
        c.setLineWidth(1)
        c.circle(cx, wire.y(cy), r, stroke=1, fill=1)
        c.restoreState()
        size = 5.4 if wire.width_of(label, 5.4) < 2 * r - 2 else 4.4
        wire.text(cx, cy - size / 2 - 1, label, size=size, color=ink, align="c")

    wire.text(centres[0], cy + 22, "query", size=5.0, color=SUBTLE, align="c")
    for idx in range(1, 4):
        wire.text(centres[idx], cy + 22, f"hop {idx}", size=5.0, color=ACCENT, align="c")


def fig_account(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=colors.HexColor("#EDEFF2"), stroke=None, radius=5)

    mx, mw = x + 66, 280
    my, mh = y + 8, h - 16
    wire.box(mx, my, mw, mh, fill=PAPER, stroke=LINE, radius=6)
    wire.text(mx + 14, my + 12, "Your account", size=6.8, color=INK)
    wire.text(mx + mw - 16, my + 12, "×", size=6.8, color=SUBTLE, align="r")
    wire.box(mx + 14, my + 24, mw - 28, 0.6, fill=LINE, stroke=None, radius=0)

    wire.box(
        mx + 14,
        my + 32,
        mw - 28,
        14,
        fill=colors.HexColor("#EAF4FA"),
        stroke=colors.HexColor("#BBD9EC"),
        radius=3,
    )
    wire.text(
        mx + 20,
        my + 37,
        "Create a free account to start searching.",
        size=5.4,
        color=colors.HexColor("#1F4E79"),
    )

    wire.segmented(mx + 14, my + 52, mw - 28, ("Sign in", "Create account"), active=1)
    wire.label(mx + 14, my + 74, "Email")
    wire.field(mx + 14, my + 80, mw - 28, "you@university.edu", state="focus")
    wire.label(mx + 14, my + 102, "Password")
    wire.field(mx + 14, my + 108, mw - 28, "At least 8 characters", state="empty")
    wire.button(mx + 14, my + 132, mw - 28, 18, "Create account", primary=True)
    wire.text(mx + 14, my + 160, "Forgot password?", size=5.4, color=ACCENT)

    wire.callout(
        x + w - 104,
        my + 58,
        "No card needed.",
        "Three free searches are waiting.",
        w=78,
    )


def fig_analyst(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=PAPER, stroke=LINE, radius=5)
    wire.text(x + 10, y + 10, "AI analyst", size=6.4, color=INK)
    wire.text(x + 10, y + 19, "Context: tubulin", size=5.0, color=SUBTLE)
    wire.pill(x + w - 40, y + 8, "online", tone="good", h=10, size=4.6)
    wire.box(x + 10, y + 28, w - 20, 0.6, fill=LINE, stroke=None, radius=0)

    for idx, line in enumerate(
        (
            "Ask anything about your results —",
            "potency comparisons, structural hints,",
            "or what to explore next.",
        )
    ):
        wire.text(x + w / 2, y + 44 + idx * 9, line, size=5.0, color=SUBTLE, align="c")

    prompts = (
        "Summarize the key findings of this search",
        "Which compounds have the strongest binding evidence?",
        "Suggest follow-up queries worth running next",
    )
    for idx, prompt in enumerate(prompts):
        py = y + 76 + idx * 14
        wire.box(x + 10, py, w - 20, 12, fill=SURFACE_2, stroke=LINE, radius=3)
        wire.text(x + 15, py + 3.6, prompt, size=5.0, color=MUTED)
        wire.text(x + w - 16, py + 3.6, "→", size=5.0, color=SUBTLE, align="r")

    by = y + 124
    wire.box(x + 10, by, w - 20, 0.6, fill=LINE, stroke=None, radius=0)
    wire.field(x + 10, by + 10, w - 74, "Ask the analyst…", state="empty", h=18)
    wire.button(x + w - 58, by + 10, 48, 18, "→", primary=True)


def fig_history(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=PAPER, stroke=LINE, radius=5)
    wire.box(x + 0.5, y + 0.5, w - 1, 22, fill=SURFACE, stroke=LINE, radius=5)
    wire.box(x + 0.5, y + 12.5, w - 1, 10, fill=SURFACE, stroke=None, radius=0)
    wire.box(x + 0.5, y + 22, w - 1, 0.6, fill=LINE, stroke=None, radius=0)
    wire.brand(x + 8, y + 4, size=5.8)
    wire.text(x + w - 100, y + 7, "Search", size=5.4, color=MUTED, align="r")
    wire.text(x + w - 70, y + 7, "History", size=5.4, color=INK, align="r")
    wire.text(x + w - 12, y + 7, "Resources", size=5.4, color=MUTED, align="r")

    wire.text(x + 12, y + 34, "Search history", size=6.6, color=INK)
    wire.text(x + w - 12, y + 35, "20 most recent, newest first", size=5.0, color=SUBTLE, align="r")

    rows = (
        ("tubulin", "Completed", "good", "a3f19c7e · 14 files · 33.8s", "Today 14:22"),
        ("Aspirin", "Completed", "good", "b71d0a44 · 15 files · 61.2s", "Today 13:05"),
        ("EGFR", "Running", "accent", "c02be918 · queued", "Today 12:48"),
    )
    for idx, (query, label, tone, meta, when) in enumerate(rows):
        ry = y + 42 + idx * 24
        wire.box(x + 10, ry, w - 20, 21, fill=PAPER, stroke=LINE, radius=4)
        wire.text(x + 16, ry + 4, query, size=5.8, color=INK)
        wire.pill(x + 16 + wire.width_of(query, 5.8) + 6, ry + 3, label, tone=tone)
        wire.text(x + 16, ry + 13.5, meta, size=4.6, color=SUBTLE, font=wire.mono)
        wire.text(x + w - 16, ry + 8, when, size=4.8, color=SUBTLE, align="r")


def fig_themes(c, x, y, w, h):
    wire = Wire(c, w, h)
    gap = 16
    half = (w - gap) / 2
    panel_h = h - 18

    def theme(ox, dark):
        page = colors.HexColor("#080A0E") if dark else PAPER
        stroke = colors.HexColor("#252B35") if dark else LINE
        text = colors.HexColor("#E8EDF3") if dark else INK
        muted = colors.HexColor("#A1ABBA") if dark else MUTED
        panel = colors.HexColor("#0F1218") if dark else SURFACE
        strong = colors.HexColor("#C7CFD9") if dark else LINE_STRONG

        wire.box(ox, y, half, panel_h, fill=page, stroke=stroke, radius=5)
        wire.box(ox + 10, y + 11, half - 20, 0.6, fill=stroke, stroke=None, radius=0)
        wire.box(ox + 10, y + 16, 11, 11, fill=ACCENT_SOFT if not dark else colors.HexColor("#0C2D36"),
                 stroke=ACCENT_LINE, radius=3)
        wire.text(ox + 25, y + 18, "Chemical Data Extractor", size=5.8, color=text)

        sx = ox + half - 44
        wire.box(sx, y + 15, 34, 13, fill=panel, stroke=stroke, radius=4)
        if dark:
            wire.box(sx + 18, y + 16.5, 15, 10, fill=page, stroke=stroke, radius=3)
            wire.text(sx + 25.5, y + 19, "☼", size=5, color=muted, align="c")
            wire.text(sx + 9, y + 19, "☾", size=5, color=text, align="c")
        else:
            wire.box(sx + 1.5, y + 16.5, 15, 10, fill=ACCENT_SOFT, stroke=ACCENT_LINE, radius=3)
            wire.text(sx + 9, y + 19, "☼", size=5, color=ACCENT, align="c")
            wire.text(sx + 25.5, y + 19, "☾", size=5, color=muted, align="c")

        wire.text(ox + 10, y + 38, "Run search", size=5.8, color=text)
        wire.box(ox + 10, y + 44, half - 20, 16, fill=panel, stroke=stroke, radius=3)
        wire.text(ox + 16, y + 49, "tubulin", size=5.4, color=muted)
        wire.box(ox + 10, y + 68, half - 20, 16, fill=panel, stroke=stroke, radius=3)
        wire.pill(ox + 16, y + 73, "Completed", tone="good" if dark else "good", h=9, size=4.4)
        wire.box(ox + 10, y + 92, half - 20, 22, fill=panel, stroke=stroke, radius=3)
        wire.text(ox + 16, y + 97, "Engine log", size=5.0, color=muted)
        wire.text(ox + 16, y + 105, "[OK] 128 bioactivities", size=4.6, color=GOOD, font=wire.mono)
        _ = strong

    theme(x, False)
    theme(x + half + gap, True)
    wire.text(x + half / 2, y + panel_h + 8, "Light mode", size=5.4, color=MUTED, align="c")
    wire.text(x + half + gap + half / 2, y + panel_h + 8, "Dark mode", size=5.4, color=MUTED, align="c")


def fig_batch(c, x, y, w, h):
    wire = Wire(c, w, h)
    wire.box(x, y, w, h, fill=colors.HexColor("#F7F8FA"), stroke=LINE, radius=5)
    lines = (
        ("# One query per line. Blank lines and # comments are ignored.", SUBTLE),
        ("Aspirin", INK),
        ("Ibuprofen", INK),
        ("P23219", INK),
        ("EGFR", INK),
        ("# protein panel for a docking run", SUBTLE),
        ("tubulin", INK),
        ("COX-2", INK),
    )
    for idx, (text, tone) in enumerate(lines):
        wire.text(x + 12, y + 12 + idx * 9, text, size=5.2, color=tone, font=wire.mono)

    cy = y + h - 22
    wire.box(x + 8, cy, w - 16, 16, fill=PAPER, stroke=LINE, radius=3)
    wire.text(x + 14, cy + 5, "python api/scigraph.py --batch batch_queries.txt",
              size=5.0, color=MUTED, font=wire.mono)


FIGURES = {
    "hops": fig_hops,
    "searchform": fig_searchform,
    "progress": fig_progress,
    "exports": fig_exports,
    "account": fig_account,
    "analyst": fig_analyst,
    "history": fig_history,
    "themes": fig_themes,
    "batch": fig_batch,
}

# (width, height) in points
FIGURE_SIZE = {
    "hops": (460, 120),
    "searchform": (460, 232),
    "progress": (460, 232),
    "exports": (460, 172),
    "account": (460, 210),
    "analyst": (460, 160),
    "history": (460, 124),
    "themes": (460, 150),
    "batch": (460, 104),
}
