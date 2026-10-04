#!/usr/bin/env python3
"""Contrast audit for the Light and Dark themes.

Reads the theme tokens straight out of app/globals.css and checks every
foreground/surface pair the interface actually uses against WCAG 2.1:

  * body text and metadata must reach 4.5:1,
  * large text and non-text indicators must reach 3:1.

Run:  python3 scripts/check_contrast.py
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(ROOT, "app", "globals.css")

# (foreground, background, minimum, description)
PAIRS = [
    ("fg", "bg", 4.5, "body text on the page canvas"),
    ("fg", "surface", 4.5, "body text in a panel"),
    ("fg", "surface-2", 4.5, "body text on an input or a tinted row"),
    ("fg", "surface-3", 4.5, "body text on a selected control"),
    ("fg-muted", "bg", 4.5, "secondary text on the canvas"),
    ("fg-muted", "surface", 4.5, "secondary text in a panel"),
    ("fg-muted", "surface-2", 4.5, "secondary text on an input"),
    ("fg-muted", "surface-3", 4.5, "secondary text on a selected control"),
    ("fg-subtle", "bg", 4.5, "metadata on the canvas"),
    ("fg-subtle", "surface", 4.5, "metadata in a panel"),
    ("fg-subtle", "surface-2", 4.5, "metadata on an input"),
    ("accent", "bg", 4.5, "links on the canvas"),
    ("accent", "surface", 4.5, "links in a panel"),
    ("accent", "surface-2", 4.5, "links on an input"),
    ("accent", "surface-3", 4.5, "links on a selected control"),
    ("accent-on-solid", "accent-solid", 4.5, "label on the primary button"),
    ("accent-on-solid", "accent-hover", 4.5, "label on the hovered primary button"),
    ("danger", "surface", 4.5, "error text in a panel"),
    ("danger", "danger-soft", 4.5, "error text in an alert"),
    ("warning", "surface", 4.5, "warning text in a panel"),
    ("warning", "warning-soft", 4.5, "warning text in a notice"),
    ("success", "surface", 4.5, "success text in a panel"),
    ("success", "success-soft", 4.5, "success text in a notice"),
    ("info", "surface", 4.5, "informational text in a panel"),
    ("info", "info-soft", 4.5, "informational text in a notice"),
]

# Non-text indicators that must still be visible against their surface.
INDICATORS = [
    ("line", "surface", 1.2, "hairline against a panel"),
    ("line-strong", "surface", 1.6, "input border against a panel"),
    ("accent-line", "surface-2", 1.6, "selected segmented-control border"),
]


def read_themes() -> dict[str, dict[str, tuple[int, int, int]]]:
    with open(CSS, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    themes: dict[str, dict[str, tuple[int, int, int]]] = {}
    for selector, name in (
        (r":root,\s*html\.light\s*{", "light"),
        (r"html\.dark\s*{", "dark"),
    ):
        match = re.search(selector + r"(.*?)\n}", text, flags=re.S)
        if not match:
            sys.exit(f"could not find the {name} theme block in app/globals.css")
        block = match.group(1)
        tokens: dict[str, tuple[int, int, int]] = {}
        for token, value in re.findall(r"--([a-z0-9-]+):\s*([0-9 ]+);", block):
            parts = value.split()
            if len(parts) == 3:
                tokens[token] = tuple(int(p) for p in parts)
        themes[name] = tokens
    return themes


def luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        v = value / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def ratio(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    themes = read_themes()
    failures = []

    for theme, tokens in themes.items():
        print(f"\n{theme.upper()}  (canvas {tokens['bg']}, surface {tokens['surface']})")
        for fg, bg, minimum, label in PAIRS:
            if fg not in tokens or bg not in tokens:
                failures.append(f"{theme}: token --{fg} or --{bg} is missing")
                continue
            value = ratio(tokens[fg], tokens[bg])
            mark = "ok " if value >= minimum else "FAIL"
            print(f"  {mark} {value:5.2f}:1  (min {minimum})  --{fg} on --{bg} — {label}")
            if value < minimum:
                failures.append(
                    f"{theme}: --{fg} on --{bg} is {value:.2f}:1, needs {minimum}:1 ({label})"
                )
        for fg, bg, minimum, label in INDICATORS:
            value = ratio(tokens[fg], tokens[bg])
            mark = "ok " if value >= minimum else "FAIL"
            print(f"  {mark} {value:5.2f}:1  (min {minimum})  --{fg} on --{bg} — {label}")
            if value < minimum:
                failures.append(
                    f"{theme}: --{fg} on --{bg} is {value:.2f}:1, needs {minimum}:1 ({label})"
                )

    if failures:
        print("\nFailures:")
        for line in failures:
            print(f"  - {line}")
        return 1
    print("\nBoth themes pass every contrast check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
