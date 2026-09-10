"""
contrast.py — check the palette against WCAG before it ships, not after.

Lighthouse checks contrast in CI, but the audit job runs *after* the deploy job, so a
failing colour reaches the live site and is reported afterwards. This runs in a second, off
the same tokens, so the failure happens on a laptop instead.

    python scripts\\contrast.py

WCAG 2.1 AA: 4.5:1 for normal text, 3:1 for large text (>=18.66px bold or >=24px) and for
non-text elements that carry meaning.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

TOKENS = Path(__file__).resolve().parent.parent / "styles" / "app.css"
AA_NORMAL = 4.5
AA_LARGE = 3.0


def channel(c: int) -> float:
    """One sRGB channel, linearised, per WCAG's relative luminance definition."""
    s = c / 255
    return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4


def luminance(hexcolour: str) -> float:
    h = hexcolour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def ratio(fg: str, bg: str) -> float:
    a, b = luminance(fg), luminance(bg)
    lo, hi = sorted((a, b))
    return (hi + 0.05) / (lo + 0.05)


def palette() -> dict[str, str]:
    """The --color-* tokens, read from the stylesheet so this cannot drift from it."""
    text = TOKENS.read_text(encoding="utf-8")
    return {m.group(1): m.group(2)
            for m in re.finditer(r"--color-([\w-]+):\s*(#[0-9a-fA-F]{6})", text)}


# Every pairing the pages actually use. A colour that is only ever a border or a fill is
# checked at the 3:1 bar; anything that carries words is checked at 4.5:1.
PAIRS = [
    # foreground, background, minimum, where it is used
    ("ink", "void", AA_NORMAL, "body text"),
    ("ink", "panel", AA_NORMAL, "body text on a panel"),
    ("ink-2", "void", AA_NORMAL, "secondary prose"),
    ("ink-2", "panel", AA_NORMAL, "secondary prose on a panel"),
    ("ink-3", "void", AA_NORMAL, "labels, footnotes, table headers"),
    ("ink-3", "panel", AA_NORMAL, "labels on a panel"),
    ("clear", "void", AA_NORMAL, "the range, links, live figures"),
    ("clear", "panel", AA_NORMAL, "the range on a panel"),
    ("caution", "void", AA_NORMAL, "below-promise figures"),
    ("caution", "panel", AA_NORMAL, "below-promise on a panel"),
    ("danger", "void", AA_NORMAL, "severe-delay callouts"),
    ("danger", "panel", AA_NORMAL, "severe-delay on a panel"),
    ("rail", "void", AA_LARGE, "drawn track, non-text"),
    ("rule", "panel", 1.0, "hairline borders, decorative only"),
]


def main() -> int:
    colours = palette()
    missing = {name for name, _, _, _ in PAIRS if name not in colours}
    missing |= {bg for _, bg, _, _ in PAIRS if bg not in colours}
    if missing:
        print(f"tokens not found in {TOKENS.name}: {', '.join(sorted(missing))}")
        return 2

    worst, failures = None, 0
    print(f"{'foreground':<9} {'background':<10} {'ratio':>6}  {'need':>5}       where")
    for fg, bg, need, where in PAIRS:
        r = ratio(colours[fg], colours[bg])
        ok = r >= need
        failures += not ok
        if worst is None or r < worst[0]:
            worst = (r, fg, bg)
        print(f"{fg:<9} {bg:<10} {r:6.2f}  {need:5.1f}  {'ok ' if ok else 'FAIL'}  {where}")

    print(f"\nthinnest margin: {worst[1]} on {worst[2]} at {worst[0]:.2f}:1")
    if failures:
        print(f"{failures} pairing(s) below the bar. Lighthouse will fail this after it ships.")
        return 1
    print("every pairing meets its bar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
