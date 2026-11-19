# RailCast logo

The mark is the product's timeline: a hollow dot where the train last reported, a line to a
filled dot at now, then a fan for the range its arrival should land in.

Drawn from the v2 brand board (`design/refs/01-brand-board-v2.webp`, generated in ChatGPT as
a reference only), then rebuilt here with exact geometry.

## Geometry (mark, viewBox 88 x 56)

- Line from the report to now: 32 units. Fan radius: 48 units (1.0 : 1.5).
- Fan: a 60° sector with apex at now, in three bands at half-angles 30°, 18° and 8°.
- Day: pale #C9CCEF, mid #7C83D6, ultramarine #2B35AF, so the centre is darkest.
  Night: the bands invert, so the centre stays the highest contrast on #0E1430.
- Wordmark: Instrument Sans (SIL OFL, `OFL-instrument-sans.txt`), weight 700, width 100,
  tracking -0.02 em. It is outlined as paths, so no font is needed to show the logo. Cap
  height is 36 units against a 48-unit fan. Rebuild it with
  `python outline_wordmark.py <InstrumentSans[wdth,wght].ttf> RailCast 700 100 -0.02`.

## Files and minimum sizes

| File | Use |
|---|---|
| `railcast-lockup.svg`, `-night` | Full lockup, at 36px tall or more |
| `railcast-lockup-compact.svg`, `-night` | Fan and wordmark, under 36px (navigation) |
| `railcast-mark.svg`, `-night` | Mark alone |
| `railcast-icon.svg` | Fan alone, transparent |
| `favicon.svg` | Browser tab: night tile, readable on light and dark tabs |

Below 36px the line and dots blur, so the compact lockup drops them. `preview.html` shows
every file at its real sizes (serve `design/` and open `/logo/preview.html`).
