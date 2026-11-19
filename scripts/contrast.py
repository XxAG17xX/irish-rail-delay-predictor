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
    # Day service, on the chalk ground and on raised paper.
    ("ink", "chalk", AA_NORMAL, "body text"),
    ("ink", "paper", AA_NORMAL, "body text on paper"),
    ("ink-2", "chalk", AA_NORMAL, "secondary prose"),
    ("ink-3", "chalk", AA_NORMAL, "labels, footnotes, table headers"),
    ("ink-3", "paper", AA_NORMAL, "labels on paper"),
    ("ultra", "chalk", AA_NORMAL, "links, the range's printed figures"),
    ("ultra", "paper", AA_NORMAL, "links on paper"),
    ("ultra", "pale", AA_NORMAL, "a figure printed inside the fan's outer band"),
    ("ink", "pale", AA_NORMAL, "selected text"),
    ("amber-ink", "chalk", AA_NORMAL, "the real arrival, in words"),
    ("ink", "amber", AA_LARGE, "ink edge around the amber arrival marker"),
    ("rule-strong", "chalk", AA_LARGE, "drawn track and map lines, non-text"),
    ("rule-strong", "paper", AA_LARGE, "drawn track on paper"),
    ("mid", "chalk", AA_LARGE, "the fan's middle band, non-text"),
    ("rule", "chalk", 1.0, "hairline borders, decorative only"),
    # Night service, when no trains are running.
    ("night-ink", "night", AA_NORMAL, "body text at night"),
    ("night-ink", "night-2", AA_NORMAL, "body text on a raised night surface"),
    ("night-ink-2", "night", AA_NORMAL, "secondary prose at night"),
    ("night-ink-3", "night", AA_NORMAL, "labels at night"),
    ("night-ink-3", "night-2", AA_NORMAL, "labels on a raised night surface"),
    ("pale", "night", AA_NORMAL, "links and the range at night"),
    ("pale", "night-2", AA_NORMAL, "links on a raised night surface"),
    ("amber", "night", AA_NORMAL, "the real arrival at night"),
    ("night-rule-strong", "night", AA_LARGE, "drawn track at night, non-text"),
    ("night-rule-strong", "night-2", AA_LARGE, "drawn track on a raised night surface"),
    ("mid", "night", AA_LARGE, "the fan's middle band at night"),
]


def main() -> int:
    colours = palette()
    missing = {name for name, _, _, _ in PAIRS if name not in colours}
    missing |= {bg for _, bg, _, _ in PAIRS if bg not in colours}
    if missing:
        print(f"tokens not found in {TOKENS.name}: {', '.join(sorted(missing))}")
        return 2

    worst, failures = None, 0
    print(f"{'foreground':<17} {'background':<8} {'ratio':>6}  {'need':>5}       where")
    for fg, bg, need, where in PAIRS:
        r = ratio(colours[fg], colours[bg])
        ok = r >= need
        failures += not ok
        if worst is None or r < worst[0]:
            worst = (r, fg, bg)
        print(f"{fg:<17} {bg:<8} {r:6.2f}  {need:5.1f}  {'ok ' if ok else 'FAIL'}  {where}")

    print(f"\nthinnest margin: {worst[1]} on {worst[2]} at {worst[0]:.2f}:1")
    if failures:
        print(f"{failures} pairing(s) below the bar. Lighthouse will fail this after it ships.")
        return 1
    print("every pairing meets its bar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
