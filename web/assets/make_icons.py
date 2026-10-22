"""Draws the two app icons once: a white J on the app's green. Run: venv/Scripts/python web/assets/make_icons.py
Uses Pillow; if that is missing, ffmpeg. The picture is plain: no effects, and the letter sits inside the safe zone
so the same file works as a round or rounded-square phone icon."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
GREEN, WHITE = "#2f5d46", "#ffffff"
FONTS = ["C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]


def with_pillow(size, dest):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for f in FONTS:
        try:
            font = ImageFont.truetype(f, int(size * 0.56))
            break
        except OSError:
            continue
    img = Image.new("RGB", (size, size), GREEN)
    d = ImageDraw.Draw(img)
    if font is None:
        font = ImageFont.load_default(int(size * 0.56))
    d.text((size / 2, size / 2), "J", fill=WHITE, font=font, anchor="mm")
    img.save(dest, "PNG", optimize=True)


def with_ffmpeg(size, dest):
    sys.path.insert(0, str(HERE.parent.parent))
    from video_tool.config import FFMPEG
    font = next((f for f in FONTS if Path(f).exists()), None)
    esc = font.replace(":", "\\:") if font else ""
    vf = f"drawtext=text=J:fontcolor=white:fontsize={int(size * 0.56)}:x=(w-text_w)/2:y=(h-text_h)/2"
    if font:
        vf += f":fontfile='{esc}'"
    subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={GREEN}:s={size}x{size}", "-vf", vf,
                    "-frames:v", "1", str(dest)], check=True)


if __name__ == "__main__":
    try:
        import PIL  # noqa: F401
        draw = with_pillow
    except ImportError:
        draw = with_ffmpeg
    for n in (192, 512):
        draw(n, HERE / f"icon-{n}.png")
        print("wrote", HERE / f"icon-{n}.png")
