#!/usr/bin/env python3
"""xiaoke: state, schedule, kitty integration and theme installation.

This script is called by the plugin's /xiaoke command and by the session-start
hook. It never edits kitty.conf; it only talks to a running kitty over remote
control (allow_remote_control socket-only + listen_on), and it edits
~/.claude/settings.json only when asked to switch the Claude Code theme.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "looks.json"
ASSETS = ROOT / "assets"
KITTY = ROOT / "kitty"
THEMES = ROOT / "themes"

LOOKS = json.loads(DATA.read_text(encoding="utf-8"))
BY_ID = {look["id"]: look for look in LOOKS}
ORDER = [
    "morning", "afternoon", "cafe", "rain", "dusk", "night", "code", "detective",
    "spring", "festival", "autumn", "snow", "holiday",
]
DEFAULT_STATE = {
    "scene": "code",
    "auto": False,
    "frame": False,
    "bg": False,
    "layout": "dock",
    "colors": False,
    "palette": False,
    "theme_sync": False,
    "timezone": None,   # minutes east of UTC, None = system local
    "pane_rows": 12,
    "dock_cols": 0,     # width the person dragged the dock to, 0 = surface default
}


# ---------------------------------------------------------------- state

def state_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "xiaoke" / "state.json"


def load_state() -> dict:
    try:
        data = json.loads(state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except json.JSONDecodeError as exc:
        # Never treat a torn or hand-broken file as "defaults": a write after
        # that would silently reset every preference.
        raise SystemExit(f"xiaoke: {state_path()} is not valid JSON ({exc}); fix or remove it")
    except OSError as exc:
        raise SystemExit(f"xiaoke: cannot read {state_path()} ({exc})")
    merged = dict(DEFAULT_STATE)
    merged.update({k: v for k, v in data.items() if k in DEFAULT_STATE})
    return merged


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


@contextlib.contextmanager
def state_lock():
    """One read-modify-write at a time, across sessions sharing the file."""
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(f"{path.name}.lock"), "w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def resolve(name: str) -> str | None:
    key = name.strip().lower()
    for look in LOOKS:
        if look["id"] == key or key in look["aliases"] or key in look["title"].lower():
            return look["id"]
    return None


def require(name: str) -> str:
    found = resolve(name)
    if found is None:
        known = ", ".join(ORDER)
        raise SystemExit(f"xiaoke: unknown look {name!r}; try one of: {known}")
    return found


# ---------------------------------------------------------------- schedule

def local_moment(offset: int | None) -> tuple[int, int, int, int, int]:
    """hour, minute, month, date, dayNumber for the configured timezone."""
    if offset is None:
        now = datetime.now()
        day = (now.date() - date(1970, 1, 1)).days
        return now.hour, now.minute, now.month, now.day, day
    now = datetime.now(timezone.utc) + timedelta(minutes=offset)
    ms = datetime.now(timezone.utc).timestamp() * 1000 + offset * 60_000
    return now.hour, now.minute, now.month, now.day, int(ms // 86_400_000)


def slot_of(hour: int) -> str:
    if 5 <= hour < 9:
        return "morning"
    if 9 <= hour < 16:
        return "day"
    if 16 <= hour < 19:
        return "dusk"
    if 19 <= hour < 23:
        return "evening"
    return "late"


def is_holidays(month: int, day: int) -> bool:
    return (month == 12 and day >= 20) or (month == 1 and day <= 6)


def season_of(month: int) -> str:
    if 3 <= month <= 5:
        return "spring"
    if 6 <= month <= 8:
        return "summer"
    if 9 <= month <= 11:
        return "autumn"
    return "winter"


def pick(items: list[str], day: int) -> str:
    return items[day % len(items)]


def candidates(hour: int, month: int, date: int, day_number: int) -> list[str]:
    slot = slot_of(hour)
    season = season_of(month)
    # Alternating picks follow the calendar day; the holiday window follows
    # the day of the month, not the epoch day.
    day = day_number - 1 if slot == "late" and hour < 5 else day_number
    if slot == "morning":
        return ["morning", "afternoon"]
    if slot == "day":
        seasonal = {"spring": "spring", "summer": None, "autumn": "autumn", "winter": "snow"}[season]
        everyday = pick(["afternoon", "cafe", "rain"], day // 2)
        first = seasonal if seasonal is not None and day % 2 == 0 else everyday
        return [first, everyday, "afternoon"]
    if slot == "dusk":
        return ["dusk", "afternoon"]
    if slot == "evening":
        if is_holidays(month, date):
            return ["holiday", "night"]
        if season == "summer" and day % 2 == 0:
            return ["festival", "night"]
        return ["night"]
    return [pick(["code", "detective"], day), "code"]


def auto_look_id(offset: int | None) -> str:
    hour, _minute, month, date, day_number = local_moment(offset)
    for name in candidates(hour, month, date, day_number):
        if name in BY_ID:
            return name
    return "afternoon"


def parse_offset(text: str) -> int | None:
    import re
    text = text.strip()
    if text.lower() in ("auto", "system", "local"):
        return None
    match = re.fullmatch(r"(?:utc|gmt)?([+-])(\d{1,2})(?::?(\d{2}))?", text, re.I)
    if match is None:
        raise SystemExit(f"xiaoke: bad timezone {text!r}; use +8, -5, +5:30 or auto")
    minutes = int(match.group(2)) * 60 + int(match.group(3) or 0)
    if minutes > 14 * 60:
        raise SystemExit("xiaoke: timezone offset must be within UTC-14..+14")
    return -minutes if match.group(1) == "-" else minutes


# ---------------------------------------------------------------- claude config

def claude_config_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def install_themes() -> list[Path]:
    dest = claude_config_dir() / "themes"
    dest.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted(THEMES.glob("xiaoke-*.json")):
        target = dest / src.name
        shutil.copyfile(src, target)
        copied.append(target)
    return copied


def set_theme_setting(scene: str) -> Path:
    path = claude_config_dir() / "settings.json"
    if path.is_file():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise SystemExit(f"xiaoke: {path} is not valid JSON ({exc}); not touching it")
    else:
        settings = {}
    backup = path.with_name("settings.json.xiaoke-bak")
    mode = (path.stat().st_mode & 0o777) if path.is_file() else 0o600
    if path.is_file() and not backup.exists():
        shutil.copyfile(path, backup)
    if backup.exists():
        os.chmod(backup, 0o600)
    settings["theme"] = f"custom:xiaoke-{scene}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(path, mode)
    return path


# ---------------------------------------------------------------- kitty

KITTEN = shutil.which("kitten") or "/usr/bin/kitten"


def kitten(*args: str) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            [KITTEN, "@", *args], capture_output=True, text=True, timeout=20,
        )
    except FileNotFoundError:
        return False, "kitten not found"
    except subprocess.TimeoutExpired:
        return False, "kitten @ timed out"
    out = (proc.stdout + proc.stderr).strip()
    return proc.returncode == 0, out


def window_match() -> list[str] | None:
    window_id = os.environ.get("KITTY_WINDOW_ID")
    if not window_id:
        # No --match would touch every kitty window on this machine.
        print("xiaoke: KITTY_WINDOW_ID is not set; refusing to touch every kitty window", file=sys.stderr)
        return None
    return ["--match", f"id:{window_id}"]


def kitty_hint() -> str:
    return (
        "kitty remote control is off; add this to ~/.config/kitty/kitty.conf and restart kitty:\n"
        "  allow_remote_control socket-only\n"
        "  listen_on unix:/tmp/kitty-{kitty_pid}"
    )


def apply_background(scene: str, layout: str = "dock") -> bool:
    sub = "docks" if layout == "dock" else "backgrounds"
    path = ASSETS / sub / f"{scene}.webp"
    if not path.is_file():
        print(f"xiaoke: missing {path}; run tools/gen_art.py", file=sys.stderr)
        return False
    match = window_match()
    if match is None:
        return False
    ok, out = kitten("set-background-image", *match, "--layout", "scaled", str(path))
    if not ok:
        print(f"xiaoke: could not set background image: {out}\n{kitty_hint()}", file=sys.stderr)
    return ok


def clear_background() -> bool:
    match = window_match()
    if match is None:
        return False
    ok, out = kitten("set-background-image", *match, "none")
    if not ok:
        print(f"xiaoke: could not clear background image: {out}\n{kitty_hint()}", file=sys.stderr)
    return ok


def apply_colors(scene: str) -> bool:
    path = KITTY / f"xiaoke-{scene}.conf"
    if not path.is_file():
        return False
    match = window_match()
    if match is None:
        return False
    ok, out = kitten("set-colors", *match, str(path))
    if not ok:
        print(f"xiaoke: could not set kitty colours: {out}\n{kitty_hint()}", file=sys.stderr)
    return ok


def reset_colors() -> bool:
    match = window_match()
    if match is None:
        return False
    ok, out = kitten("set-colors", *match, "--reset")
    if not ok:
        print(f"xiaoke: could not reset kitty colours: {out}\n{kitty_hint()}", file=sys.stderr)
    return ok


def apply(scene: str, state: dict, quiet: bool = False) -> None:
    """Optionally sync kitty's palette or the Claude Code theme.

    Both are off by default: the user keeps their own dark terminal theme.
    The illustration frame does not depend on either.
    """
    if state["theme_sync"]:
        install_themes()
        path = set_theme_setting(scene)
        if not quiet:
            print(f"theme -> custom:xiaoke-{scene} ({path})")
    if state["colors"]:
        if apply_colors(scene) and not state["palette"]:
            state["palette"] = True
            save_state(state)
    elif state["palette"]:
        reset_colors()
        state["palette"] = False
        save_state(state)


def refresh_background(state: dict) -> bool:
    """Re-apply the illustration after a scene change while the frame is up.

    The pane stays open, so its blank cells would otherwise keep showing the
    previous scene until the frame is closed and reopened.
    """
    if not state["bg"]:
        return True
    return apply_background(state["scene"], state["layout"])


# ---------------------------------------------------------------- commands

def describe(look: dict) -> str:
    aliases = "/".join(look["aliases"])
    return f"{look['id']:<10} {look['mode']:<5} {look['title']}  ({aliases})"


def cmd_list(_args) -> int:
    for ident in ORDER:
        print(describe(BY_ID[ident]))
    return 0


def cmd_current(_args) -> int:
    state = load_state()
    look = BY_ID.get(state["scene"])
    if look is None:
        print(f"scene   {state['scene']}  (unknown)")
    else:
        print(f"scene   {state['scene']}  {look['title']}")
    print(f"auto    {'on' if state['auto'] else 'off'}")
    print(f"frame   {'on' if state['frame'] else 'off'}")
    print(f"bg      {'on' if state['bg'] else 'off'} ({state['layout']})")
    print(f"colors  {'on' if state['colors'] else 'off'}")
    print(f"palette {'scene' if state['palette'] else 'terminal'}")
    print(f"theme   {'sync' if state['theme_sync'] else 'manual'}")
    dock = state["dock_cols"] or "auto"
    print(f"pane    {state['pane_rows']} rows, {dock} columns")
    return 0


def cmd_get(args) -> int:
    state = load_state()
    if args.key not in state:
        raise SystemExit(f"xiaoke: no such key {args.key!r}")
    value = state[args.key]
    if isinstance(value, bool):
        print("on" if value else "off")
    elif value is None:
        print("auto")
    else:
        print(value)
    return 0


def cmd_set(args) -> int:
    scene = require(args.name)
    state = load_state()
    state["scene"] = scene
    # A manual pick no longer turns the schedule off: auto keeps running and
    # the next tick (up to ten minutes) follows the time again.
    save_state(state)
    apply(scene, state, quiet=args.quiet)
    refresh_background(state)
    if not args.quiet:
        print(f"小克 · {BY_ID[scene]['title']}  [{scene}]")
    return 0


def cmd_auto(args) -> int:
    state = load_state()
    mode = args.mode
    if mode == "toggle":
        state["auto"] = not state["auto"]
    else:
        state["auto"] = mode == "on"
    save_state(state)
    if state["auto"]:
        scene = auto_look_id(state["timezone"])
        if scene != state["scene"]:
            state["scene"] = scene
            save_state(state)
        apply(scene, state)
        refresh_background(state)
        print(f"auto on -> {scene}")
    else:
        print("auto off")
    return 0


def cmd_tick(args) -> int:
    state = load_state()
    if not state["auto"]:
        return 0
    scene = auto_look_id(state["timezone"])
    if scene != state["scene"]:
        state["scene"] = scene
        save_state(state)
        apply(scene, state, quiet=args.quiet)
        refresh_background(state)
        if not args.quiet:
            print(f"auto -> {scene}  {BY_ID[scene]['title']}")
    return 0


def cmd_bg(args) -> int:
    state = load_state()
    if getattr(args, "layout", None):
        state["layout"] = args.layout
    before = state["bg"]
    if args.mode == "toggle":
        state["bg"] = not state["bg"]
    else:
        state["bg"] = args.mode == "on"
    save_state(state)
    if state["bg"]:
        ok = apply_background(state["scene"], state["layout"])
    else:
        ok = clear_background()
    if not ok:
        state["bg"] = before
        save_state(state)
        print(f"background {'on' if before else 'off'} (kitty command failed; state unchanged)")
        return 1
    print(f"background {'on' if state['bg'] else 'off'} ({state['layout']})")
    return 0


def cmd_frame(args) -> int:
    """Persist the pane preference; the mod opens/closes the actual Pane."""
    state = load_state()
    if args.mode == "toggle":
        state["frame"] = not state["frame"]
    else:
        state["frame"] = args.mode == "on"
    save_state(state)
    print("on" if state["frame"] else "off")
    return 0


def cmd_rows(args) -> int:
    state = load_state()
    rows = int(args.rows)
    if not 5 <= rows <= 40:
        raise SystemExit("xiaoke: pane rows must be between 5 and 40")
    state["pane_rows"] = rows
    save_state(state)
    print(f"pane rows {rows} (reopen the frame to apply)")
    return 0


def cmd_dock(args) -> int:
    state = load_state()
    cols = int(args.cols)
    if not 1 <= cols <= 500:
        raise SystemExit("xiaoke: pane columns must be between 1 and 500")
    state["dock_cols"] = cols
    save_state(state)
    print(f"pane width {cols}")
    return 0


def cmd_shutdown(args) -> int:
    """One call at session end: remember the width, then clear the image."""
    state = load_state()
    cols = int(args.cols)
    if cols > 0:
        state["dock_cols"] = cols
    if state["bg"]:
        state["bg"] = False
    save_state(state)
    ok = clear_background()
    print("background off" if ok else "background off failed")
    return 0 if ok else 1


def cmd_sync(args) -> int:
    state = load_state()
    if args.mode == "toggle":
        state["theme_sync"] = not state["theme_sync"]
    else:
        state["theme_sync"] = args.mode == "on"
    save_state(state)
    print(f"theme sync {'on' if state['theme_sync'] else 'off'}")
    return 0


def cmd_colors(args) -> int:
    state = load_state()
    if args.mode == "toggle":
        state["colors"] = not state["colors"]
    else:
        state["colors"] = args.mode == "on"
    save_state(state)
    if state["colors"]:
        ok = apply_colors(state["scene"])
        state["palette"] = ok
    else:
        ok = reset_colors()
        state["palette"] = False
    save_state(state)
    print(f"kitty colours {'on' if state['colors'] else 'default'}" + ("" if ok else " (kitty command failed)"))
    return 0 if ok else 1


def cmd_theme(args) -> int:
    scene = require(args.name) if args.name else load_state()["scene"]
    install_themes()
    path = set_theme_setting(scene)
    print(f"theme -> custom:xiaoke-{scene} ({path})")
    print("Claude Code may need a new session (or /theme) to show it.")
    return 0


def cmd_tz(args) -> int:
    state = load_state()
    state["timezone"] = parse_offset(args.offset)
    save_state(state)
    if state["timezone"] is None:
        print("timezone auto")
    else:
        sign = "+" if state["timezone"] >= 0 else "-"
        minutes = abs(state["timezone"])
        print(f"timezone UTC{sign}{minutes // 60}" + (f":{minutes % 60:02d}" if minutes % 60 else ""))
    if state["auto"]:
        return cmd_tick(argparse.Namespace(quiet=False))
    return 0


def cmd_session_start(args) -> int:
    state = load_state()
    if state["auto"]:
        scene = auto_look_id(state["timezone"])
        if scene != state["scene"]:
            state["scene"] = scene
            save_state(state)
    apply(state["scene"], state, quiet=True)
    return 0


def cmd_install(_args) -> int:
    for path in install_themes():
        print(path)
    return 0


def cmd_path(args) -> int:
    state = load_state()
    scene = require(args.name) if args.name else state["scene"]
    sub = "docks" if state["layout"] == "dock" else "backgrounds"
    print(ASSETS / sub / f"{scene}.webp")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="xiaoke", description="小克 look for Claude Code CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="list looks").set_defaults(func=cmd_list)
    sub.add_parser("current", help="show current state").set_defaults(func=cmd_current)

    get = sub.add_parser("get", help="print one state value")
    get.add_argument("key")
    get.set_defaults(func=cmd_get)

    setp = sub.add_parser("set", help="switch look")
    setp.add_argument("name")
    setp.add_argument("--quiet", action="store_true")
    setp.set_defaults(func=cmd_set)

    auto = sub.add_parser("auto", help="follow time of day and season")
    auto.add_argument("mode", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    auto.set_defaults(func=cmd_auto)

    tick = sub.add_parser("tick", help="apply the auto look if it changed")
    tick.add_argument("--quiet", action="store_true")
    tick.set_defaults(func=cmd_tick)

    bg = sub.add_parser("bg", help="window background image for the docked pane")
    bg.add_argument("mode", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    bg.add_argument("layout", nargs="?", choices=["dock", "inline"])
    bg.set_defaults(func=cmd_bg)

    frame = sub.add_parser("frame", help="remember whether the inline pane is wanted")
    frame.add_argument("mode", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    frame.set_defaults(func=cmd_frame)

    rows = sub.add_parser("rows", help="height of the inline pane, in rows")
    rows.add_argument("rows")
    rows.set_defaults(func=cmd_rows)

    dock = sub.add_parser("dock", help="remember the docked pane's width in columns")
    dock.add_argument("cols")
    dock.set_defaults(func=cmd_dock)

    sync = sub.add_parser("sync", help="let the look write the Claude Code theme setting")
    sync.add_argument("mode", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    sync.set_defaults(func=cmd_sync)

    colors = sub.add_parser("colors", help="apply the look to kitty's palette")
    colors.add_argument("mode", nargs="?", choices=["on", "off", "toggle"], default="toggle")
    colors.set_defaults(func=cmd_colors)

    theme = sub.add_parser("theme", help="write the Claude Code theme setting")
    theme.add_argument("name", nargs="?")
    theme.set_defaults(func=cmd_theme)

    tz = sub.add_parser("tz", help="set the auto timezone (auto, +8, -5, +5:30)")
    tz.add_argument("offset")
    tz.set_defaults(func=cmd_tz)

    sub.add_parser("install", help="copy themes into ~/.claude/themes").set_defaults(func=cmd_install)
    sub.add_parser("session-start", help="apply the saved look").set_defaults(func=cmd_session_start)

    path = sub.add_parser("path", help="print the background image path")
    path.add_argument("name", nargs="?")
    path.set_defaults(func=cmd_path)

    down = sub.add_parser("shutdown", help="save the dock width and clear the image")
    down.add_argument("cols", nargs="?", default="0")
    down.set_defaults(func=cmd_shutdown)

    args = parser.parse_args()
    with state_lock():
        return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
