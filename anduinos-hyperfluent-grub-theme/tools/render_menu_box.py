"""Render the original slim blue GRUB menu frame as nine scalable PNG slices.

Run this only when changing the artwork. The installed theme uses the PNGs;
neither Pillow nor this generator is needed at boot or when building the deb.
"""

from pathlib import Path

from PIL import Image, ImageDraw


THEME = Path(__file__).resolve().parent.parent / "assets/theme"
SIDE = 64
CORNER = 16


def main() -> None:
    frame = Image.new("RGBA", (SIDE, SIDE), (0, 0, 0, 0))
    paint = ImageDraw.Draw(frame)

    # A dark blue panel suppresses bright wallpaper details without turning
    # the whole menu into the black block used by the previous slices.
    paint.rounded_rectangle(
        (2, 2, SIDE - 3, SIDE - 3), radius=9,
        fill=(16, 27, 45, 222),
        outline=(67, 112, 171, 116), width=2,
    )

    cuts = (0, CORNER, SIDE - CORNER, SIDE)
    for row, vertical in enumerate(("n", "", "s")):
        for column, horizontal in enumerate(("w", "", "e")):
            suffix = vertical + horizontal or "c"
            frame.crop((cuts[column], cuts[row], cuts[column + 1], cuts[row + 1])).save(
                THEME / f"menu_box_{suffix}.png"
            )


if __name__ == "__main__":
    main()
