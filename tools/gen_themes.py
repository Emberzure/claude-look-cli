#!/usr/bin/env python3
"""Generate Claude Code custom themes from data/looks.json.

The palette is the upstream xiaoke-look palette (roles + anchors + paper).
Claude Code custom themes are {name, base, overrides}; values may be
#rrggbb, rgb(), ansi256() or ansi:<name>. Token names are the engine's own
(2.1.330); unknown tokens are silently ignored, so we check them here.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "looks.json"
OUT = ROOT / "themes"

# Every colour token the engine's dark/light presets carry (2.1.330).
TOKENS = {
    "clawd_background", "userMessageBackground", "userMessageBackgroundHover",
    "composerSidebarBackground", "selectionBg", "bashMessageBackgroundColor",
    "memoryBackgroundColor", "rate_limit_fill", "rate_limit_empty", "fastMode",
    "fastModeShimmer", "effortUltra", "briefLabelYou", "briefLabelClaude",
    "rainbow_red", "rainbow_orange", "rainbow_yellow", "rainbow_green",
    "rainbow_blue", "rainbow_indigo", "rainbow_violet",
    "rainbow_red_shimmer", "rainbow_orange_shimmer", "rainbow_yellow_shimmer",
    "rainbow_green_shimmer", "rainbow_blue_shimmer", "rainbow_indigo_shimmer",
    "rainbow_violet_shimmer", "autoAccept", "autoAcceptShimmer", "skill",
    "bashBorder", "claude", "claudeShimmer", "claudeBlue_FOR_SYSTEM_SPINNER",
    "claudeBlueShimmer_FOR_SYSTEM_SPINNER", "permission", "permissionShimmer",
    "planMode", "ide", "promptBorder", "promptBorderShimmer", "text",
    "inverseText", "inactive", "inactiveShimmer", "subtle", "suggestion",
    "remember", "background", "success", "error", "warning", "merged",
    "warningShimmer", "diffAdded", "diffRemoved", "diffAddedDimmed",
    "diffRemovedDimmed", "diffAddedWord", "diffRemovedWord",
    "red_FOR_SUBAGENTS_ONLY", "blue_FOR_SUBAGENTS_ONLY",
    "green_FOR_SUBAGENTS_ONLY", "yellow_FOR_SUBAGENTS_ONLY",
    "purple_FOR_SUBAGENTS_ONLY", "orange_FOR_SUBAGENTS_ONLY",
    "pink_FOR_SUBAGENTS_ONLY", "cyan_FOR_SUBAGENTS_ONLY",
    "professionalBlue", "chromeYellow", "clawd_body",
}


def rgb(color: str) -> tuple[int, int, int]:
    value = color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def hexed(value: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in value)


def mix(a: str, b: str, t: float) -> str:
    ar, ag, ab = rgb(a)
    br, bg, bb = rgb(b)
    return hexed((ar + (br - ar) * t, ag + (bg - ag) * t, ab + (bb - ab) * t))


def shift(color: str, mode: str, t: float) -> str:
    """Brighten on dark paper, darken on light paper."""
    return mix(color, "#ffffff" if mode == "dark" else "#000000", t)


def luminance(color: str) -> float:
    def channel(c: float) -> float:
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb(color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def on_color(background: str) -> str:
    dark, light = "#0b0b0b", "#ffffff"
    return dark if contrast(background, dark) >= contrast(background, light) else light


def build(look: dict) -> dict:
    mode = look["mode"]
    paper = look["paper"]
    roles = look["roles"]
    anchors = look["anchors"]
    surf1 = anchors.get("surface-1", mix(paper, "#ffffff" if mode == "light" else "#000000", 0.04))
    surf2 = anchors.get("surface-2", surf1)
    surf3 = anchors.get("surface-3", surf2)
    tp = anchors["text-primary"]
    ts = anchors["text-secondary"]
    tm = anchors["text-muted"]

    accent, danger, success, warning, pro = (
        roles["accent"], roles["danger"], roles["success"], roles["warning"], roles["pro"],
    )
    accent_hi = shift(accent, mode, 0.18)
    danger_hi = shift(danger, mode, 0.18)
    success_hi = shift(success, mode, 0.18)
    warning_hi = shift(warning, mode, 0.18)
    pro_hi = shift(pro, mode, 0.18)

    o = {
        "claude": accent,
        "claudeShimmer": accent_hi,
        "claudeBlue_FOR_SYSTEM_SPINNER": pro,
        "claudeBlueShimmer_FOR_SYSTEM_SPINNER": pro_hi,
        "text": tp,
        "inverseText": on_color(accent),
        "inactive": tm,
        "inactiveShimmer": ts,
        "subtle": tm,
        "suggestion": pro,
        "remember": pro,
        "success": success,
        "error": danger,
        "warning": warning,
        "warningShimmer": warning_hi,
        "merged": pro,
        "diffAdded": success,
        "diffRemoved": danger,
        "diffAddedDimmed": mix(success, paper, 0.55),
        "diffRemovedDimmed": mix(danger, paper, 0.55),
        "diffAddedWord": success_hi,
        "diffRemovedWord": danger_hi,
        "autoAccept": pro,
        "autoAcceptShimmer": pro_hi,
        "skill": pro,
        "bashBorder": accent,
        "permission": pro,
        "permissionShimmer": pro_hi,
        "planMode": pro,
        "ide": pro,
        "promptBorder": ts,
        "promptBorderShimmer": tp,
        "fastMode": warning,
        "fastModeShimmer": warning_hi,
        "effortUltra": pro,
        "briefLabelYou": pro,
        "briefLabelClaude": accent,
        "userMessageBackground": surf2,
        "userMessageBackgroundHover": surf3,
        "composerSidebarBackground": surf1,
        "bashMessageBackgroundColor": surf1,
        "memoryBackgroundColor": surf1,
        "selectionBg": mix(accent, paper, 0.55),
        "rate_limit_fill": accent,
        "rate_limit_empty": surf2,
        "clawd_background": paper,
        "clawd_body": accent,
        "professionalBlue": pro,
        "chromeYellow": warning,
        "red_FOR_SUBAGENTS_ONLY": danger,
        "blue_FOR_SUBAGENTS_ONLY": pro,
        "green_FOR_SUBAGENTS_ONLY": success,
        "yellow_FOR_SUBAGENTS_ONLY": warning,
        "purple_FOR_SUBAGENTS_ONLY": pro,
        "orange_FOR_SUBAGENTS_ONLY": accent,
        "pink_FOR_SUBAGENTS_ONLY": mix(danger, accent, 0.5),
        "cyan_FOR_SUBAGENTS_ONLY": mix(pro, success, 0.5),
        "rainbow_red": danger,
        "rainbow_orange": accent,
        "rainbow_yellow": warning,
        "rainbow_green": success,
        "rainbow_blue": pro,
        "rainbow_indigo": mix(pro, "#0000ff", 0.35),
        "rainbow_violet": mix(pro, accent, 0.45),
        "rainbow_red_shimmer": danger_hi,
        "rainbow_orange_shimmer": accent_hi,
        "rainbow_yellow_shimmer": warning_hi,
        "rainbow_green_shimmer": success_hi,
        "rainbow_blue_shimmer": pro_hi,
        "rainbow_indigo_shimmer": shift(mix(pro, "#0000ff", 0.35), mode, 0.18),
        "rainbow_violet_shimmer": shift(mix(pro, accent, 0.45), mode, 0.18),
    }

    unknown = sorted(set(o) - TOKENS)
    if unknown:
        raise SystemExit(f"{look['id']}: unknown Claude Code theme tokens: {unknown}")

    return {
        "name": f"小克 · {look['title']}",
        "base": mode,
        "overrides": dict(sorted(o.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="verify colours and tokens only")
    args = parser.parse_args()

    looks = json.loads(DATA.read_text(encoding="utf-8"))
    OUT.mkdir(parents=True, exist_ok=True)
    seen = set()
    for look in looks:
        if look["id"] in seen:
            raise SystemExit(f"duplicate id {look['id']}")
        seen.add(look["id"])
        theme = build(look)
        if args.check:
            print(f"{look['id']}: ok ({len(theme['overrides'])} tokens)")
            continue
        path = OUT / f"xiaoke-{look['id']}.json"
        path.write_text(json.dumps(theme, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()
