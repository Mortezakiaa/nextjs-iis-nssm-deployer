"""Everything the operator sees. Persian text is reshaped and put in visual order so it reads correctly
in the classic Windows console (which has no RTL support). Set NEXTDEPLOYER_TEXT=raw for terminals that do
their own RTL layout."""
import ctypes
import os
import sys
import textwrap

import arabic_reshaper
from bidi import get_display
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

console = Console(highlight=False)
RAW = os.environ.get("NEXTDEPLOYER_TEXT", "").lower() == "raw"
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def fa(s: str) -> str:
    if RAW:
        return s
    return get_display(arabic_reshaper.reshape(s)).replace("‌", "")  # ZWNJ already applied by shaping


# ---------------------------------------------------------------- console setup
class _COORD(ctypes.Structure):
    _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]


class _FONTINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("nFont", ctypes.c_ulong), ("dwFontSize", _COORD),
                ("FontFamily", ctypes.c_uint), ("FontWeight", ctypes.c_uint), ("FaceName", ctypes.c_wchar * 32)]


def init_console() -> None:
    for s in (sys.stdout, sys.stderr, sys.stdin):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if os.name != "nt":
        return
    try:
        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleCP(65001)
        k.SetConsoleTitleW("NextDeployer")
        if not os.environ.get("WT_SESSION"):
            # Classic console has no font fallback; Consolas/Lucida lack Persian glyphs, Courier New has them all.
            h = k.GetStdHandle(-11)
            f = _FONTINFO()
            f.cbSize = ctypes.sizeof(f)
            if k.GetCurrentConsoleFontEx(h, False, ctypes.byref(f)):
                f.FaceName = "Courier New"
                f.FontFamily = 54  # FF_MODERN | TMPF_TRUETYPE | TMPF_VECTOR
                f.FontWeight = 400
                f.dwFontSize.X, f.dwFontSize.Y = 0, max(f.dwFontSize.Y, 18)
                k.SetCurrentConsoleFontEx(h, False, ctypes.byref(f))
    except Exception:
        pass


# ---------------------------------------------------------------- output
def say(msg: str, style: str = "") -> None:
    width = max(30, console.width - 2)
    for para in str(msg).split("\n"):
        for line in textwrap.wrap(para, width) or [""]:
            console.print(Text(fa(line), style=style), justify="left" if RAW else "right")


def title(msg: str) -> None:
    console.print()
    console.print(Panel(Text(fa(msg), justify="center", style="bold"), border_style="cyan"))


def step(i: int, n: int, msg: str) -> None:
    say(f"مرحله {i} از {n}: {msg}", "bold cyan")


def info(msg: str) -> None:
    say(msg)


def ok(msg: str) -> None:
    say(msg, "bold green")


def warn(msg: str) -> None:
    say(msg, "bold yellow")


def error(msg: str) -> None:
    say(msg, "bold red")


def box(caption: str, lines: list) -> None:
    """Raw technical text (log tails) shown as-is, left-to-right."""
    console.print(Panel(Text("\n".join(lines) or "-"), title=Text(fa(caption)), border_style="red"))


def busy(msg: str):
    return console.status(Text(fa(msg)), spinner="line")


# ---------------------------------------------------------------- input
def _read() -> str:
    try:
        return console.input("> ").strip().translate(_DIGITS)
    except EOFError:
        raise KeyboardInterrupt from None


def ask(question: str, default: str = "") -> str:
    say(question, "bold")
    if default:
        say(f"(اگر Enter بزنید این مقدار استفاده می‌شود: {default})", "dim")
    return _read() or default


def ask_path(question: str, default: str = "") -> str:
    say("نکته: می‌توانید فایل یا پوشه را با ماوس داخل همین پنجره بکشید و رها کنید.", "dim")
    return ask(question, default).strip().strip('"').strip("'").strip()


def choose(question: str, options: list) -> int:
    """Numbered menu, returns the 0-based index."""
    while True:
        say(question, "bold")
        for i, o in enumerate(options, 1):
            say(f"{i}- {o}")
        a = _read()
        if a.isdigit() and 1 <= int(a) <= len(options):
            return int(a) - 1
        warn(f"لطفاً فقط یک عدد بین 1 تا {len(options)} وارد کنید.")


def confirm(question: str) -> bool:
    return choose(question, ["بله", "خیر"]) == 0


def pause(msg: str = "برای ادامه کلید Enter را بزنید.") -> None:
    say(msg, "dim")
    try:
        console.input()
    except (EOFError, KeyboardInterrupt):
        pass
