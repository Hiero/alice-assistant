"""The clipboard, currency conversion and finding files in the user's home folder."""

import asyncio
import json
import os
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

CLIPBOARD_LIMIT = 8000
CLIPBOARD_PREFIX = "Untrusted text the user copied"
RATES_URL = "https://api.frankfurter.dev/v1/latest"
FILE_RESULTS = 20
# Folders full of things nobody means by "my file".
SKIPPED_DIRS = ["node_modules", ".git", ".cache", ".local/share/Steam", ".local/share/Trash", "__pycache__", ".venv", "venv"]


# Clipboard


async def clipboard_read() -> str:
    """The copied text, read only when the user asks about it."""
    types = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=5).stdout.split()
    if not types:
        return "The clipboard is empty."
    text_type = next((t for t in types if t.startswith("text/plain")), next((t for t in types if t.startswith("text/")), None))
    if text_type is None:
        kind = "an image" if any(t.startswith("image/") for t in types) else "something that isn't text"
        return f"The clipboard holds {kind}, which I can't read."
    found = subprocess.run(["wl-paste", "--no-newline", "--type", text_type], capture_output=True, timeout=5).stdout
    text = found.decode(errors="replace")
    cut = f"\n[Only the first {CLIPBOARD_LIMIT} of {len(text)} characters.]" if len(text) > CLIPBOARD_LIMIT else ""
    return f"{CLIPBOARD_PREFIX} ({len(text)} characters):\n{text[:CLIPBOARD_LIMIT]}{cut}"


async def preview_clipboard_copy(text: str) -> str:
    return text if len(text) <= 1500 else text[:1500] + f"\n… ({len(text)} characters in all)"


async def clipboard_copy(text: str) -> str:
    if not text:
        raise ValueError("There is nothing to copy.")
    # wl-copy stays running to serve the clipboard, so it must not be waited on.
    process = await asyncio.create_subprocess_exec("wl-copy", stdin=asyncio.subprocess.PIPE, start_new_session=True)
    process.stdin.write(text.encode())
    await process.stdin.drain()
    process.stdin.close()
    return "Copied to the clipboard."


# Currency


async def convert_currency(amount: float = 1, source: str = "USD", target: str = "BRL") -> str:
    source, target = source.strip().upper(), target.strip().upper()
    amount = float(amount or 1)
    if source == target:
        return f"{amount:,.2f} {source} is {amount:,.2f} {target}."
    url = f"{RATES_URL}?{urllib.parse.urlencode({'from': source, 'symbols': target})}"

    def get() -> dict:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "alice-assistant"}), timeout=10) as response:
            return json.load(response)

    try:
        data = await asyncio.to_thread(get)
    except urllib.error.HTTPError as error:
        raise ValueError(f"I don't have rates for {source} to {target}. Use currency codes like USD, EUR or BRL.") from error
    except OSError as error:
        raise RuntimeError(f"Couldn't get exchange rates: {error}") from error
    rate = data["rates"][target]
    return (
        f"{amount:,.2f} {source} = {amount * rate:,.2f} {target} (1 {source} = {rate:.4f} {target}, "
        f"European Central Bank rate of {data['date']}; banks and card payments add their own fees)."
    )


# Files


def human_size(n_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n_bytes < 1024:
            return f"{n_bytes:.0f} {unit}"
        n_bytes /= 1024
    return f"{n_bytes:.1f} TB"


def find_paths(query: str, extension: str, folders: bool, everywhere: bool) -> list[Path]:
    words = [w for w in query.casefold().split() if w]
    if not words:
        raise ValueError("Say what the file is called, or part of its name.")
    argv = ["fd", "--ignore-case", "--absolute-path", "--type", "d" if folders else "f", "--max-results", "400"]
    if not everywhere:
        argv.append("--one-file-system")  # Other drives mounted in home, like a big media disk, are slow to walk.
    for skipped in SKIPPED_DIRS:
        argv += ["--exclude", skipped]
    if extension:
        argv += ["--extension", extension.lstrip(".")]
    # fd matches one pattern, so search for the longest word and filter the rest here.
    argv += ["--fixed-strings", max(words, key=len), str(Path.home())]
    try:
        found = subprocess.run(argv, capture_output=True, text=True, timeout=90 if everywhere else 20).stdout.splitlines()
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("Searching took too long. Try a more specific name.") from error
    paths = [Path(p) for p in found if all(w in p.casefold() for w in words)]
    return sorted(paths, key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)[:FILE_RESULTS]


async def find_files(query: str, extension: str = "", folders: bool = False, everywhere: bool = False) -> tuple[str, dict]:
    paths = await asyncio.to_thread(find_paths, query, extension, bool(folders), bool(everywhere))
    kind = "folders" if folders else "files"
    event = {
        "type": "links",
        "title": f"{kind.capitalize()} matching {query} ({len(paths)})",
        "items": [
            {
                "name": p.name,
                "url": p.as_uri(),
                "detail": f"{str(p.parent).replace(str(Path.home()), '~')} · {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d}"
                + ("" if folders else f" · {human_size(p.stat().st_size)}"),
            }
            for p in paths
        ],
    }
    if not paths:
        where = "anywhere in the home folder, including other drives" if everywhere else "on the main drive of the home folder"
        hint = "" if everywhere else " Other drives weren't searched; offer to search everywhere, which is slower."
        return f"No {kind} {where} match {query!r}.{hint}", event
    lines = [
        f"{p} | modified {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M}" + ("" if folders else f" | {human_size(p.stat().st_size)}")
        for p in paths
    ]
    return (
        f"{len(paths)} {kind} match, newest first. They're shown to the user as clickable links, so mention the likely one or two "
        "rather than reading every path:\n" + "\n".join(lines),
        event,
    )


def checked_path(path: str) -> Path:
    target = Path(os.path.expanduser(path.strip())).resolve()
    if not target.exists():
        raise ValueError(f"There is no file or folder at {path}.")
    return target


async def preview_open_file(path: str) -> str:
    target = checked_path(path)
    return f"{target}\n(opens in its usual app)"


async def open_file(path: str) -> str:
    target = checked_path(path)
    subprocess.Popen(["xdg-open", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return f"Opened {target.name}."
