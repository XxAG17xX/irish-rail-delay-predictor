r"""Draw site/og.png, the link preview card.

Run after changing the wording on it:

    .venv\Scripts\python.exe scripts\make_og_image.py

1200x630 is what LinkedIn, Slack and the rest crop to. The card is generated rather than
hand-drawn so the palette can never drift from styles/app.css, and it is committed because
the deploy only syncs site/ and nothing on the runner could redraw it.

The face is Segoe UI Bold, not the site's Bricolage Grotesque: the real face ships as woff2,
which Pillow cannot read, and converting it here would be a build step for one image.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

VOID, INK, INK_2, INK_3, RAIL, CLEAR = (
    "#0b0f10", "#e4ecea", "#93a5a3", "#748a8d", "#54696d", "#34d17c",
)
OUT = Path(__file__).resolve().parent.parent / "site" / "og.png"
W, H = 1200, 630


def face(size, bold=True):
    for name in ("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def main():
    img = Image.new("RGB", (W, H), VOID)
    d = ImageDraw.Draw(img)

    # The rail runs the full width, with the reported stop lit and the ones ahead open:
    # the same object the site draws on every diagram.
    y = 318
    d.line([(90, y), (W - 90, y)], fill=RAIL, width=3)
    for x, lit in ((300, True), (560, False), (820, False)):
        r = 11
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=CLEAR if lit else VOID, outline=CLEAR if lit else INK_3, width=3)
    # The span the arrival should land in, drawn over the track it belongs to.
    d.line([(560, y), (820, y)], fill=CLEAR, width=9)

    d.text((90, 150), "RailCast", font=face(112), fill=INK)
    d.text((90, 360), "Irish Rail delays, as a range", font=face(52), fill=INK_2)
    d.text((90, 434), "Not 17:27. Between 17:22 and 17:34, four times in five.",
           font=face(34, bold=False), fill=INK_3)
    d.text((90, 520), "Every prediction is scored the next morning, wins and losses alike.",
           font=face(30, bold=False), fill=INK_3)

    OUT.write_bytes(b"")  # fail early if the path is not writable
    img.save(OUT, "PNG", optimize=True)
    print(f"{OUT}  {OUT.stat().st_size / 1024:.0f} KB  {W}x{H}")


if __name__ == "__main__":
    main()
