"""Regenerate unlock artwork only when changing the design; never run at build.

Uses the existing GRUB font tool and ffmpeg SVG renderer. The logo source is
shared with Driver Center; PF2 files contain only printable ASCII. Font license
is kept in font-copyright. No graphics tools are needed on the installed OS.
"""
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent
ACCENT = "#3281ea"  # Same blue as the recovery environment.


def run(*arguments):
    subprocess.run(arguments, check=True)


def main():
    with tempfile.TemporaryDirectory() as directory:
        temporary = Path(directory)
        background = temporary / "background.svg"
        background.write_text('''<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="800">
<defs><radialGradient id="glow"><stop stop-color="#164382"/><stop offset="1" stop-color="#101722"/></radialGradient></defs>
<rect width="1280" height="800" fill="#101722"/>
<ellipse cx="640" cy="260" rx="900" ry="700" fill="url(#glow)"/>
<rect x="64" y="24" width="1152" height="752" rx="24" fill="#182436" stroke="#31415a"/>
</svg>''')
        run("ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(background), str(ROOT / "background.png"))
        frame = temporary / "frame.svg"
        frame.write_text(f'''<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64">
<rect x="1" y="1" width="62" height="62" rx="10" fill="#101b2b" stroke="{ACCENT}" stroke-width="2"/>
</svg>''')
        png = temporary / "frame.png"
        run("ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(frame), str(png))
        cuts = (0, 16, 48, 64)
        for row, vertical in enumerate(("n", "", "s")):
            for column, horizontal in enumerate(("w", "", "e")):
                suffix = vertical + horizontal or "c"
                x, y = cuts[column], cuts[row]
                width, height = cuts[column + 1] - x, cuts[row + 1] - y
                run("ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(png),
                    "-vf", f"crop={width}:{height}:{x}:{y}", str(ROOT / f"terminal_box_{suffix}.png"))
    logo = ROOT.parents[1] / "anduinos-driver-center/resources/anduinos-logo.svg"
    run("ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(logo), "-vf", "scale=72:72", str(ROOT / "logo.png"))
    for source, size, name in (("DejaVuSans-Bold.ttf", 32, "title"),
                               ("DejaVuSans.ttf", 18, "body"),
                               ("DejaVuSansMono.ttf", 14, "terminal")):
        run("grub-mkfont", "-s", str(size), "-r", "0x20-0x7e", "-o", str(ROOT / f"{name}.pf2"),
            "/usr/share/fonts/truetype/dejavu/" + source)


if __name__ == "__main__":
    main()
