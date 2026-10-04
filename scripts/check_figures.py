#!/usr/bin/env python3
"""Geometry checks for the documentation figures.

The PDFs cannot be eyeballed in this environment, so every figure is drawn onto
a recording canvas and each element is checked against its frame:

  * nothing may be drawn outside the figure's box,
  * text may not overlap other text (glyph boxes, not line boxes),
  * figures must fill a sensible share of the page width.

Run:  python3 scripts/check_figures.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_resource_pdfs as pdf  # noqa: E402
import pdf_figures as figs  # noqa: E402


class Box:
    def __init__(self, x0, y0, x1, y1, label):
        self.x0, self.y0, self.x1, self.y1, self.label = x0, y0, x1, y1, label


class Recorder:
    """Stands in for the canvas: records geometry, draws nothing."""

    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.boxes: list[Box] = []
        self.texts: list[Box] = []
        self._wire = figs.Wire(self, width, height)

    def note_text(self, x, y, s, size, font, align):
        """Glyph box of a string drawn at top-down y (baseline at y + size)."""
        width = pdf.pdfmetrics.stringWidth(s, font, size)
        if align == "c":
            x0 = x - width / 2
        elif align == "r":
            x0 = x - width
        else:
            x0 = x
        cap = 0.72 * size
        self.texts.append(
            Box(x0, y + size - cap, x0 + width, y + size + 0.22 * size, f"text {s!r}")
        )

    # ── Wire proxy: the layout helpers, unchanged ──────────────────────────
    def __getattr__(self, item):
        return getattr(self._wire, item)

    def box(self, x, y, w, h, **kw):
        self._wire.box(x, y, w, h, **kw)
        self.boxes.append(Box(x, y, x + w, y + h, "box"))

    def text(self, x, y, s, size=6.2, color=None, font=None, align="l"):
        self._wire.text(x, y, s, size=size, color=color, font=font, align=align)

    # ── Canvas surface ─────────────────────────────────────────────────────
    def saveState(self):
        pass

    def restoreState(self):
        pass

    def setStrokeColor(self, *a, **k):
        pass

    def setFillColor(self, *a, **k):
        pass

    def setLineWidth(self, *a, **k):
        pass

    def setDash(self, *a, **k):
        pass

    def setFont(self, *a, **k):
        pass

    def circle(self, x, y, r, **k):
        self.boxes.append(Box(x - r, y - r, x + r, y + r, "circle"))

    def line(self, x1, y1, x2, y2, **k):
        self.boxes.append(
            Box(min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2), "line")
        )

    def roundRect(self, x, y, w, h, r, **k):
        self.boxes.append(Box(x, y, x + w, y + h, "roundRect"))

    def rect(self, x, y, w, h, **k):
        self.boxes.append(Box(x, y, x + w, y + h, "rect"))

    def drawString(self, x, y, s, **k):
        pass

    def drawCentredString(self, x, y, s, **k):
        pass

    def drawRightString(self, x, y, s, **k):
        pass


def overlaps(a: Box, b: Box) -> bool:
    return not (a.x1 <= b.x0 + 0.1 or b.x1 <= a.x0 + 0.1 or a.y1 <= b.y0 + 0.1 or b.y1 <= a.y0 + 0.1)


def check(name: str, available_width: float) -> list[str]:
    width, height = figs.FIGURE_SIZE[name]
    rec = Recorder(width, height)
    figs.FIGURES[name](rec, 0, 0, width, height)

    problems = []
    if width > available_width + 0.5:
        problems.append(f"{name}: {width}pt wider than the {available_width:.0f}pt text column")
    for b in rec.boxes + rec.texts:
        if b.x0 < -0.5 or b.y0 < -0.5 or b.x1 > width + 0.5 or b.y1 > height + 0.5:
            problems.append(
                f"{name}: {b.label} outside frame "
                f"({b.x0:.1f},{b.y0:.1f})-({b.x1:.1f},{b.y1:.1f}) in {width}x{height}"
            )
    for i, a in enumerate(rec.texts):
        for b in rec.texts[i + 1 :]:
            if overlaps(a, b):
                problems.append(f"{name}: text overlap {a.label} / {b.label}")
    return problems


def main() -> int:
    pdf.register_fonts()
    problems: list[str] = []
    print(f"  body font: {figs.BODY_FONT}, mono font: {figs.MONO_FONT}")
    for name in sorted(figs.FIGURES):
        found = check(name, pdf.CONTENT_W)
        status = "ok" if not found else f"{len(found)} problem(s)"
        print(f"  {name:12s} {figs.FIGURE_SIZE[name]}  {status}")
        problems += found
    if problems:
        print("\n".join(problems))
        return 1
    print("\nAll figures fit their frames, clear each other, and match the text column.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
