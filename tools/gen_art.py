#!/usr/bin/env python3
"""Build the three image products from the upstream illustrations.

  assets/frames/<id>.webp       1024x1536 portrait, 92% alpha
  assets/banners/<id>.webp      1200x420 face crop, feathered (inline pane band)
  assets/backgrounds/<id>.webp  1200x1500 transparent canvas with that band
  assets/docks/<id>.webp        1706x1066 canvas, portrait in the right 44%

The banner is placed in the lower-middle of the background so a transparent
Pane above the prompt has the artwork behind it. Tune BAND_Y if the pane sits
higher or lower on your screen.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = ROOT / ".cache" / "illustrations"
ASSETS = ROOT / "assets"
BAND_Y = 930          # top of the banner on the 1200x1500 canvas
BANNER_W, BANNER_H = 1200, 420
CANVAS_W, CANVAS_H = 1200, 1500
DOCK_W, DOCK_H = 1706, 1066   # the output's logical size
DOCK_FRACTION = 0.44          # right-side dock pane, under the 45% cap


def magick(*args: str) -> None:
    subprocess.run(["magick", *args], check=True)


def build_frame(src: Path, dest: Path) -> None:
    magick(str(src), "-resize", "1024x1536!", "-alpha", "set",
           "-channel", "A", "-evaluate", "multiply", "0.92", "+channel",
           "-quality", "88", str(dest))


def build_banner(src: Path, dest: Path, face: tuple[float, float]) -> None:
    crop_h = round(1024 / (BANNER_W / BANNER_H))          # 358
    cy = round(face[1] * 1536)
    y = max(0, min(1536 - crop_h, cy - crop_h // 2))
    mask = dest.with_suffix(".mask.png")
    magick("-size", f"{BANNER_W}x{BANNER_H}", "xc:black",
           "-fill", "white", "-draw",
           "roundrectangle 70,10 1130,410 28,28", "-blur", "0x22", str(mask))
    magick(str(src), "-crop", f"1024x{crop_h}+0+{y}", "+repage",
           "-resize", f"{BANNER_W}x{BANNER_H}!",
           str(mask), "-alpha", "off", "-compose", "CopyOpacity", "-composite",
           "-channel", "A", "-evaluate", "multiply", "0.96", "+channel",
           "-quality", "88", str(dest))
    mask.unlink(missing_ok=True)


def build_dock(src: Path, dest: Path) -> None:
    art_w = round(DOCK_W * DOCK_FRACTION)
    art_x = DOCK_W - art_w
    art = dest.with_suffix(".dock.png")
    mask = dest.with_suffix(".dockmask.png")
    magick(str(src), "-resize", f"{art_w}x{DOCK_H}^", "-gravity", "north",
           "-extent", f"{art_w}x{DOCK_H}", str(art))
    # Fade the left edge into the chat and the bottom edge into the prompt.
    magick("-size", f"{art_w}x{DOCK_H}", "xc:white",
           "-fill", "black", "-draw", f"rectangle 0,0 150,{DOCK_H}",
           "-draw", f"rectangle 0,{DOCK_H - 70} {art_w},{DOCK_H}",
           "-blur", "0x38", str(mask))
    magick(str(art), str(mask), "-alpha", "off", "-compose", "CopyOpacity",
           "-composite", str(art))
    magick("-size", f"{DOCK_W}x{DOCK_H}", "xc:none",
           str(art), "-geometry", f"+{art_x}+0", "-compose", "over",
           "-composite", "-quality", "88", str(dest))
    art.unlink(missing_ok=True)
    mask.unlink(missing_ok=True)


def build_background(banner: Path, dest: Path, paper: str) -> None:
    panel = dest.with_suffix(".panel.png")
    magick("-size", f"{CANVAS_W}x{CANVAS_H}", "xc:none",
           "-fill", paper + "8c", "-draw",
           f"roundrectangle 24,{BAND_Y - 70} {CANVAS_W - 24},{BAND_Y + BANNER_H + 70} 48,48",
           "-blur", "0x30", str(panel))
    magick("-size", f"{CANVAS_W}x{CANVAS_H}", "xc:none",
           str(panel), "-compose", "over", "-composite",
           str(banner), "-geometry", f"+0+{BAND_Y}", "-compose", "over", "-composite",
           "-quality", "88", str(dest))
    panel.unlink(missing_ok=True)


def main() -> None:
    looks = json.loads((ROOT / "data" / "looks.json").read_text(encoding="utf-8"))
    missing = [look["id"] for look in looks if not (CACHE / f"{look['id']}.png").is_file()]
    if missing:
        sys.exit(f"missing illustrations: {missing}\nrun tools/fetch_art.sh first")

    for sub in ("frames", "banners", "backgrounds", "docks"):
        (ASSETS / sub).mkdir(parents=True, exist_ok=True)

    for look in looks:
        ident = look["id"]
        src = CACHE / f"{ident}.png"
        build_frame(src, ASSETS / "frames" / f"{ident}.webp")
        banner = ASSETS / "banners" / f"{ident}.webp"
        build_banner(src, banner, tuple(look["face"]))
        build_background(banner, ASSETS / "backgrounds" / f"{ident}.webp", look["paper"])
        build_dock(src, ASSETS / "docks" / f"{ident}.webp")
        print(f"{ident}: frame + banner + background + dock")


if __name__ == "__main__":
    main()
