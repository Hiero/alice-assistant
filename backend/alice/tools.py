"""Tools Alice can call.

A tool with a `preview` changes something and always needs the user's approval. The
server enforces that, and the text shown is built here from the real arguments, never
from the model's description of what it is doing.
"""

import asyncio
import base64
import configparser
import json
import os
import re
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


from alice.llm import IS_LOCAL, MODEL, REASONING_ARGS, client
from alice.occasions import WARDROBE, forget_birthday, outfit_event, preview_forget_birthday, preview_remember_birthday, remember_birthday, today, wear
from alice.movies import movie_info, movie_reviews
from alice.news import news
from alice.websearch import web_search
from alice.weather import weather
from alice.steam import steam_releases
from alice.steamlibrary import steam_game, steam_recommend, steam_reviews, steam_top_played, steam_top_rated, steam_wishlist
from alice.system import system_status
from alice.desktop import clipboard_copy, clipboard_read, convert_currency, find_files, open_file, preview_clipboard_copy, preview_open_file
from alice.music import find_music, music_control, now_playing, play_music, play_similar, playlist_names, queue_music, set_volume

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
NOTES_DIR = Path(os.environ.get("ALICE_NOTES_DIR", DATA_HOME / "alice/notes"))
UNIT_DIR = CONFIG_HOME / "systemd/user"
MEMORY_FILE = Path(os.environ.get("ALICE_MEMORY_FILE", DATA_HOME / "alice/memory.md"))
MAX_MEMORIES = 100
ALARM_SOUND = Path("/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga")

MAX_OUTPUT_CHARS = 8000
COMMAND_TIMEOUT_SECONDS = 60


async def run_process_bytes(*argv: str, timeout: float = 15) -> bytes:
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(process.communicate(), timeout)
    if process.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace").strip() or f"{argv[0]} failed")
    return stdout


async def run_process(*argv: str, timeout: float = 15) -> str:
    return (await run_process_bytes(*argv, timeout=timeout)).decode(errors="replace")


# Clock and shell


async def preview_command(command: str) -> str:
    return command


async def run_command(command: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "bash",
        "-c",
        command,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), COMMAND_TIMEOUT_SECONDS)
    except TimeoutError:
        os.killpg(process.pid, signal.SIGKILL)
        await process.wait()
        return f"Timed out after {COMMAND_TIMEOUT_SECONDS} seconds and was killed."
    text = output.decode(errors="replace")[-MAX_OUTPUT_CHARS:]
    return f"Exit code {process.returncode}\n{text}"


# Windows and applications


async def fetch_windows() -> list[dict]:
    return json.loads(await run_process("niri", "msg", "-j", "windows"))


async def list_windows() -> str:
    fields = ("id", "title", "app_id", "workspace_id", "is_focused")
    return json.dumps([{k: w.get(k) for k in fields} for w in await fetch_windows()], ensure_ascii=False)


async def describe_window(window_id: int) -> str:
    for window in await fetch_windows():
        if window["id"] == window_id:
            return f"{window.get('title') or 'Untitled'} ({window.get('app_id')})"
    raise ValueError(f"No window with id {window_id}")


async def focus_window(window_id: int) -> str:
    await run_process("niri", "msg", "action", "focus-window", "--id", str(window_id))
    return "Focused."


async def close_window(window_id: int) -> str:
    await run_process("niri", "msg", "action", "close-window", "--id", str(window_id))
    return "Close requested."


def installed_apps() -> dict[str, tuple[str, Path]]:
    data_dirs = [DATA_HOME, *map(Path, os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"))]
    apps: dict[str, tuple[str, Path]] = {}
    for directory in data_dirs:
        for path in sorted((directory / "applications").glob("*.desktop")):
            if path.stem in apps:
                continue
            parser = configparser.RawConfigParser(strict=False, interpolation=None)
            try:
                parser.read(path, encoding="utf-8")
                entry = parser["Desktop Entry"]
            except (configparser.Error, UnicodeDecodeError, KeyError):
                continue
            if entry.get("NoDisplay", "").lower() != "true" and entry.get("Name"):
                apps[path.stem] = (entry["Name"], path)
    return apps


async def list_apps(query: str = "") -> str:
    needle = query.lower()
    matches = [
        {"app_id": app_id, "name": name}
        for app_id, (name, _) in installed_apps().items()
        if needle in app_id.lower() or needle in name.lower()
    ]
    return json.dumps(matches[:50], ensure_ascii=False)


def find_app(app_id: str) -> tuple[str, Path]:
    app = installed_apps().get(app_id)
    if app is None:
        raise ValueError(f"No installed app with id {app_id}. Use the apps search action to find it.")
    return app


async def preview_open_app(app_id: str) -> str:
    return find_app(app_id)[0]


async def open_app(app_id: str) -> str:
    _, path = find_app(app_id)
    await run_process("niri", "msg", "action", "spawn", "--", "gio", "launch", str(path))
    return "Launched."


# Screen


async def look_at_screen(question: str) -> str:
    """Answer a question from one screenshot, so no image ever enters the chat history."""
    if not IS_LOCAL:
        raise ValueError("Screen viewing is only allowed with a local model.")
    output = json.loads(await run_process("niri", "msg", "-j", "focused-output"))["name"]
    image = await run_process_bytes("grim", "-t", "jpeg", "-q", "85", "-o", output, "-")
    data_url = "data:image/jpeg;base64," + base64.b64encode(image).decode()
    response = await client.chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": f"This is a screenshot of the user's desktop. {question}"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        **REASONING_ARGS,
    )
    return response.choices[0].message.content or "The model returned no description."


# Web search


# Long-term memory: one Markdown bullet per fact, editable by hand.


def read_memories() -> list[str]:
    if not MEMORY_FILE.exists():
        return []
    lines = (line.strip() for line in MEMORY_FILE.read_text().splitlines())
    return [line.removeprefix("- ").strip() for line in lines if line.startswith("- ")]


def write_memories(memories: list[str]) -> None:
    MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    MEMORY_FILE.write_text("".join(f"- {memory}\n" for memory in memories))


def clean_memory(fact: str) -> str:
    cleaned = " ".join(fact.split())
    if not cleaned:
        raise ValueError("There is nothing to remember.")
    return cleaned


async def preview_remember(fact: str) -> str:
    return clean_memory(fact)


async def remember(fact: str) -> str:
    memories = read_memories()
    cleaned = clean_memory(fact)
    if cleaned in memories:
        return "Already remembered."
    if len(memories) >= MAX_MEMORIES:
        raise ValueError(f"Memory is full ({MAX_MEMORIES} items). Forget something first.")
    write_memories([*memories, cleaned])
    return "Remembered."


def find_memory(fact: str) -> str:
    needle = fact.strip().lower()
    matches = [memory for memory in read_memories() if needle and needle in memory.lower()]
    if len(matches) != 1:
        raise ValueError(f"Expected one matching memory but found {len(matches)}: {matches}")
    return matches[0]


async def preview_forget(fact: str) -> str:
    return find_memory(fact)


async def forget(fact: str) -> str:
    target = find_memory(fact)
    write_memories([memory for memory in read_memories() if memory != target])
    return "Forgotten."


# Notes


def note_path(title: str) -> Path:
    slug = re.sub(r"[^\w-]+", "-", title.strip().lower()).strip("-")
    if not slug:
        raise ValueError("The note needs a title.")
    return NOTES_DIR / f"{slug}.md"


async def list_notes() -> str:
    return json.dumps(sorted(p.stem for p in NOTES_DIR.glob("*.md")), ensure_ascii=False)


async def read_note(title: str) -> str:
    return note_path(title).read_text()[-MAX_OUTPUT_CHARS:]


async def preview_add_note(title: str, text: str) -> str:
    return f"{note_path(title)}\n\n{text}"


async def add_note(title: str, text: str) -> str:
    path = note_path(title)
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with path.open("a") as note:
        note.write(f"## {stamp}\n\n{text.strip()}\n\n")
    return f"Saved to {path}."


# Alarms are real unit files because transient systemd timers are lost on reboot.


def parse_future(when: str) -> datetime:
    moment = datetime.fromisoformat(when).replace(microsecond=0)
    if moment.tzinfo is not None:
        moment = moment.astimezone().replace(tzinfo=None)
    if moment <= datetime.now():
        raise ValueError(f"{moment} is in the past.")
    return moment


def unit_quote(text: str) -> str:
    flat = " ".join(text.split())
    escaped = flat.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + escaped.replace("%", "%%").replace("$", "$$") + '"'


def alarm_units(alarm_id: str) -> tuple[Path, Path]:
    return UNIT_DIR / f"alice-alarm-{alarm_id}.timer", UNIT_DIR / f"alice-alarm-{alarm_id}.service"


async def systemctl(*args: str) -> None:
    await run_process("systemctl", "--user", *args)


async def alarm_is_loaded(alarm_id: str) -> bool:
    process = await asyncio.create_subprocess_exec(
        "systemctl", "--user", "is-active", "--quiet", f"alice-alarm-{alarm_id}.timer"
    )
    return await process.wait() == 0


async def remove_alarm_files(alarm_id: str) -> None:
    timer, service = alarm_units(alarm_id)
    for path in (timer, service, UNIT_DIR / "timers.target.wants" / timer.name):
        path.unlink(missing_ok=True)
    await systemctl("daemon-reload")


async def active_alarms() -> list[dict]:
    alarms = []
    for timer in sorted(UNIT_DIR.glob("alice-alarm-*.timer")):
        alarm_id = timer.stem.removeprefix("alice-alarm-")
        if await alarm_is_loaded(alarm_id):
            header = timer.read_text().splitlines()[0].removeprefix("# ")
            alarms.append(json.loads(header))
        else:
            await remove_alarm_files(alarm_id)
    return alarms


async def list_alarms() -> str:
    return json.dumps(await active_alarms(), ensure_ascii=False)


async def preview_set_alarm(when: str, message: str) -> str:
    return f"{parse_future(when):%Y-%m-%d %H:%M:%S}\n{message}"


async def set_alarm(when: str, message: str) -> str:
    moment = parse_future(when)
    alarm_id = moment.strftime("%Y%m%d%H%M%S")
    timer_path, service_path = alarm_units(alarm_id)
    if timer_path.exists():
        raise ValueError("An alarm already exists at that exact time.")
    UNIT_DIR.mkdir(parents=True, exist_ok=True)

    sound = f"ExecStart=-/usr/bin/pw-play {ALARM_SOUND}\n" if ALARM_SOUND.exists() else ""
    notify = f"/usr/bin/notify-send -u critical -a Alice Alice {unit_quote(message)}"
    service_path.write_text(f"[Unit]\nDescription=Alice alarm\n\n[Service]\nType=oneshot\nExecStart={notify}\n{sound}")
    header = json.dumps({"id": alarm_id, "when": moment.isoformat(sep=" "), "message": message}, ensure_ascii=False)
    timer_path.write_text(
        f"# {header}\n[Unit]\nDescription=Alice alarm\n\n"
        f"[Timer]\nOnCalendar={moment:%Y-%m-%d %H:%M:%S}\nAccuracySec=1s\n"
        "Persistent=true\nRemainAfterElapse=false\n\n[Install]\nWantedBy=timers.target\n"
    )
    await systemctl("daemon-reload")
    await systemctl("start", timer_path.name)
    await systemctl("enable", timer_path.name)
    return f"Alarm {alarm_id} set for {moment}."


async def find_alarm(alarm_id: str) -> dict:
    for alarm in await active_alarms():
        if alarm["id"] == alarm_id:
            return alarm
    raise ValueError(f"No alarm with id {alarm_id}.")


async def preview_cancel_alarm(alarm_id: str) -> str:
    alarm = await find_alarm(alarm_id)
    return f"{alarm['when']}\n{alarm['message']}"


async def cancel_alarm(alarm_id: str) -> str:
    await find_alarm(alarm_id)
    await systemctl("disable", "--now", f"alice-alarm-{alarm_id}.timer")
    await remove_alarm_files(alarm_id)
    return "Alarm cancelled."


# Registry


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    properties: dict
    run: Callable[..., Awaitable[str | tuple[str, dict]]]
    confirm_title: str = ""
    # Shows what an action will do before the user approves it: a detail, a (title, detail) pair,
    # or None when this particular call needs no approval.
    preview: Callable[..., Awaitable[str | tuple[str, str] | None]] | None = None
    reads_web: bool = False
    optional: tuple[str, ...] = ()
    status: str = ""  # Shown while a web tool runs, instead of its search query.
    keeps_result: bool = False  # A web tool whose latest result stays in her memory for follow-ups.
    free_text: str = ""  # The argument a web tool sends out as typed text, so a second call asks first.


@dataclass(frozen=True)
class Action:
    """One action of a grouped tool: the function, the arguments it takes, and its approval, if any."""

    run: Callable[..., Awaitable]
    needs: tuple[str, ...] = ()
    takes: tuple[str, ...] = ()  # Optional arguments.
    preview: Callable[..., Awaitable[str]] | None = None
    title: str = ""


def group(name: str, description: str, actions: dict[str, Action], properties: dict, **flags) -> Tool:
    """Several related tools as one, chosen by an action argument, so the model has fewer tools to pick from."""

    def pick(action: str, arguments: dict) -> tuple[Action, dict]:
        if action not in actions:
            raise ValueError(f"Unknown action {action!r}. Use one of: {', '.join(actions)}.")
        chosen = actions[action]
        given = {k: v for k, v in arguments.items() if v not in (None, "")}
        missing = [k for k in chosen.needs if k not in given]
        if missing:
            raise ValueError(f"The {action} action needs {' and '.join(missing)}.")
        return chosen, {k: given[k] for k in (*chosen.needs, *chosen.takes) if k in given}

    async def run(action: str = "", **arguments):
        chosen, kept = pick(action, arguments)
        return await chosen.run(**kept)

    async def preview(action: str = "", **arguments):
        chosen, kept = pick(action, arguments)
        if chosen.preview is None:
            return None
        return chosen.title, await chosen.preview(**kept)

    schema = {"action": {"type": "string", "enum": list(actions), "description": "What to do"}, **properties}
    return Tool(name, description, schema, run, preview=preview, optional=tuple(properties), **flags)


def text_param(description: str) -> dict:
    return {"type": "string", "description": description}


def integer_param(description: str) -> dict:
    return {"type": "integer", "description": description}


async def preview_window(window_id: int) -> str:
    return await describe_window(window_id)


async def steam_releases_day(day: str = "today", refresh: bool = False):
    return await steam_releases(day, refresh)


async def steam_recommend_day(day: str = "", like: str = ""):
    # Matching one named game needs a bigger pool than a single day's releases.
    return await steam_recommend(day or ("this week" if like.strip() else "today"), like)


async def steam_top(count: int = 10):
    return await steam_top_played(count)


async def music_action(action: str):
    return await music_control(action)


async def change_outfit(name: str):
    """Her own clothes, so no approval: the shell is told to redraw her, and the queen and evil looks also change her voice."""
    worn = wear(name.strip().lower())
    event = outfit_event(today())
    if worn == "evil":
        return f"You're now wearing {WARDROBE[worn]}. Your sharper, sarcastic self takes over from your next words.", event
    if worn == "queen":
        return f"You're now wearing {WARDROBE[worn]}. Your proud, refined queenly manner takes over from your next words.", event
    if worn:
        return f"You're now wearing {WARDROBE[worn]}.", event
    return "You're back in your usual clothes, and your usual sweet self.", event


TOOLS = {
    tool.name: tool
    for tool in (
        group(
            "apps",
            "Installed applications. search: find apps by name and get their app_id. open: start one by its app_id from search; always search first, since an app_id is rarely just its name.",
            {"search": Action(list_apps, takes=("query",)), "open": Action(open_app, ("app_id",), preview=preview_open_app, title="Open this app?")},
            {"query": text_param("For search: text to look for in app names"), "app_id": text_param("For open: the app_id from search")},
        ),
        group(
            "windows",
            "Open windows. list: see them with their ids, titles and workspaces. focus or close: one window by its id from list.",
            {
                "list": Action(list_windows),
                "focus": Action(focus_window, ("window_id",), preview=preview_window, title="Focus this window?"),
                "close": Action(close_window, ("window_id",), preview=preview_window, title="Close this window?"),
            },
            {"window_id": integer_param("For focus and close: the window id from list")},
        ),
        group(
            "notes",
            "The user's saved notes. list: their titles. read: one note by title. add: append text to a note, creating it if needed.",
            {
                "list": Action(list_notes),
                "read": Action(read_note, ("title",)),
                "add": Action(add_note, ("title", "text"), preview=preview_add_note, title="Save this note?"),
            },
            {"title": text_param("For read and add: the note title"), "text": text_param("For add: the text to add")},
        ),
        group(
            "alarms",
            "Alarms that show a notification and play a sound. list: pending alarms. set: a new alarm. cancel: one by its id from list.",
            {
                "list": Action(list_alarms),
                "set": Action(set_alarm, ("when", "message"), preview=preview_set_alarm, title="Set this alarm?"),
                "cancel": Action(cancel_alarm, ("alarm_id",), preview=preview_cancel_alarm, title="Cancel this alarm?"),
            },
            {
                "when": text_param("For set: local time as YYYY-MM-DDTHH:MM:SS"),
                "message": text_param("For set: the alarm message"),
                "alarm_id": text_param("For cancel: the alarm id from list"),
            },
        ),
        group(
            "music",
            "The user's Jellyfin music library and what's playing. search: find songs, artists or albums. "
            "play: start a song, album, artist, playlist, the user's favorites or random music. queue: add a song, album or "
            "artist after what's playing. similar: play songs like a named song, or with no query like the song playing now (no need to check it first), "
            "which it finds by itself, so don't check now_playing first. "
            "pause, resume, next, previous, stop: control playback. now_playing: what is playing. playlists: list playlists.",
            {
                "search": Action(find_music, ("query",)),
                "play": Action(play_music, takes=("query", "kind", "artist")),
                **{name: Action(lambda name=name: music_action(name)) for name in ("pause", "resume", "next", "previous", "stop")},
                "now_playing": Action(now_playing),
                "queue": Action(queue_music, takes=("query", "kind", "artist")),
                "similar": Action(play_similar, takes=("query",)),
                "playlists": Action(playlist_names),
            },
            {
                "query": text_param("For search, play, queue and similar: a song title, artist, album or playlist name, without other words. Empty for random or favorites, and for similar to what's playing."),
                "kind": text_param("For play and queue: song, album, artist, playlist, favorites or random. Use artist for a random song by an artist, random only for the whole library."),
                "artist": text_param("For play: the artist, if the user gave one for a song or album"),
            },
        ),
        Tool(
            "volume",
            "Change or report volume. Use target music unless the user means the computer or system.",
            {
                "level": text_param('Percent like "50", a change like "+10" or "-10", or empty to report it'),
                "target": text_param("One of: music, system"),
            },
            set_volume,
            optional=("level",),
        ),
        group(
            "memory",
            "Facts the user asked you to keep. remember: save one. forget: delete one. Birthdays use the birthdays tool.",
            {
                "remember": Action(remember, ("fact",), preview=preview_remember, title="Remember this?"),
                "forget": Action(forget, ("fact",), preview=preview_forget, title="Forget this?"),
            },
            {"fact": text_param("A short, self-contained fact, like: The user's browser is Zen. For forget: text that identifies one saved memory.")},
        ),
        group(
            "birthdays",
            "Birthdays to greet on the day. remember: save one when the user tells you it. forget: delete one. Use person user for the user's own.",
            {
                "remember": Action(remember_birthday, ("person", "month", "day"), preview=preview_remember_birthday, title="Remember this birthday?"),
                "forget": Action(forget_birthday, ("person",), preview=preview_forget_birthday, title="Forget this birthday?"),
            },
            {
                "person": text_param("Whose birthday, like user, Mom or Lucas"),
                "month": integer_param("For remember: month number, 1 to 12"),
                "day": integer_param("For remember: day of the month"),
            },
        ),
        Tool(
            "outfit",
            "Change your own clothes, only when the user asks you to. queen: your Queen Alice outfit from Through the "
            "Looking-Glass, with a crown. evil: Evil Alice, with black hair, red eyes, dark clothes and a sarcastic tongue. "
            "usual: back to your normal dress and self.",
            {"name": {"type": "string", "enum": ["usual", *WARDROBE], "description": "Which outfit to wear"}},
            change_outfit,
        ),
        Tool(
            "web_search",
            "Search the internet and read the top pages, for facts you don't know or that may have changed: games and their "
            "characters, people, places, dates, prices, how-tos and recent events.",
            {"query": text_param("Search terms")},
            web_search,
            reads_web=True,
            free_text="query",
        ),
        Tool(
            "news",
            "Find recent news headlines about a topic from Google News. Use it for news, not for general facts.",
            {
                "query": text_param("The topic, like a name, company or event, without words like news or latest"),
                "days": integer_param("How many days back, 1 to 30. Default 2."),
                "edition": text_param("us for English news, br for Brazilian news in Portuguese. Use br for topics about Brazil."),
            },
            news,
            reads_web=True,
            optional=("days", "edition"),
            free_text="query",
        ),
        Tool(
            "weather",
            "Get the detailed weather now and the forecast for a place: temperature, feels like, rain, wind, humidity, "
            "UV, air quality, sunrise and sunset, hourly for a day and daily for up to 16 days.",
            {
                "location": text_param("City, optionally with region or country, like Campinas, Sao Paulo. If the user named no place, use their home city from what you remember, otherwise leave it empty. Never guess a city."),
                "days": integer_param("Days of forecast, 3 to 16, counting today. Use enough to reach the day asked about. Default 3."),
            },
            weather,
            optional=("location", "days"),
        ),
        group(
            "steam",
            "The Steam store and the user's Steam games. game: store facts about one named game: whether it is out yet, its release "
            "date, its price and sale, and its review rating. Not for a game's story, characters or gameplay, which need web_search. "
            "reviews: what players say about a game, from its most helpful reviews. "
            "top_rated: the best reviewed games of a genre, or the ones most like a game the user names; use it to recommend "
            "games that aren't new releases. all_releases: every game released on a day. "
            "recommended_releases: only the new releases the user would like, matched to their most played games or to "
            "a game they name. top_played: the user's most played games. "
            "wishlist: the user's wishlist games that are on sale. The clickable lists only appear when you call this, so call it "
            "every time the user asks, even if you answered before.",
            {
                "all_releases": Action(steam_releases_day, takes=("day", "refresh")),
                "recommended_releases": Action(steam_recommend_day, takes=("day", "like")),
                "top_played": Action(steam_top, takes=("count",)),
                "game": Action(steam_game, ("game",)),
                "reviews": Action(steam_reviews, ("game",)),
                "top_rated": Action(steam_top_rated, takes=("genre", "like")),
                "wishlist": Action(steam_wishlist),
            },
            {
                "day": text_param("For all_releases and recommended_releases: the time the user said, in their words, like today, yesterday, November 5, this week or last 3 days. Default today."),
                "like": text_param("For recommended_releases and top_rated: a game the user wants something similar to, only if they named one"),
                "game": text_param("For game and reviews: the game's name"),
                "genre": text_param("For top_rated: the genre or kind of game in the user's words, like roguelike, cozy or souls-like"),
                "count": integer_param("For top_played: how many games, 1 to 30. Default 10."),
                "refresh": {"type": "boolean", "description": "For all_releases: fetch again even if saved, only when the user asks for updated reviews"},
            },
            reads_web=True,
            status="Checking Steam…",
            keeps_result=True,
        ),
        group(
            "movies",
            "Films and TV series (not video games, which use steam or web_search), without spoilers. info: the premise, genres, length or seasons, age rating, cast, "
            "IMDb, Rotten Tomatoes and Metacritic scores, awards, and which streaming services have it. "
            "reviews: what critics and viewers think of it.",
            {"info": Action(movie_info, ("title",), takes=("kind", "year")), "reviews": Action(movie_reviews, ("title",), takes=("kind", "year"))},
            {
                "title": text_param("The film or series name, in the user's words"),
                "kind": text_param("movie or series, only if the user said which"),
                "year": text_param("Its year, only if the user gave one, like 1982"),
            },
            reads_web=True,
            status="Looking it up…",
        ),
        group(
            "system",
            "Read-only facts about this computer, no approval needed. overview: CPU, memory, GPU, temperatures, disks and uptime. "
            "disks: free space. temperatures: CPU, GPU, SSD and RAM heat. processes: what uses the most memory and CPU.",
            {name: Action(lambda name=name: system_status(name)) for name in ("overview", "disks", "temperatures", "processes")},
            {},
        ),
        group(
            "clipboard",
            "The clipboard. read: the text the user copied, only when they ask about it, like to translate, summarize or save it. "
            "copy: put text on the clipboard for them.",
            {"read": Action(clipboard_read), "copy": Action(clipboard_copy, ("text",), preview=preview_clipboard_copy, title="Copy this to the clipboard?")},
            {"text": text_param("For copy: the exact text to copy")},
        ),
        Tool(
            "convert_currency",
            "Convert money between currencies at today's European Central Bank rate, like dollars to reais.",
            {
                "amount": {"type": "number", "description": "How much money. Default 1."},
                "source": text_param("Currency code to convert from, like USD, EUR or BRL"),
                "target": text_param("Currency code to convert to. Default BRL."),
            },
            convert_currency,
            optional=("amount", "target"),
        ),
        group(
            "files",
            "Files and folders in the user's home folder. find: search by name, newest first, shown as clickable links. "
            "open: open a file or folder in its usual app.",
            {
                "find": Action(find_files, ("query",), takes=("extension", "folders", "everywhere")),
                "open": Action(open_file, ("path",), preview=preview_open_file, title="Open this?"),
            },
            {
                "query": text_param("For find: words from the name, like resume or tax 2025"),
                "extension": text_param("For find: only this file type, like pdf, if the user said one"),
                "folders": {"type": "boolean", "description": "For find: look for folders instead of files"},
                "everywhere": {"type": "boolean", "description": "For find: also search other drives, which is slow. Only when the user asks or nothing was found."},
                "path": text_param("For open: the full path from find"),
            },
        ),
        Tool(
            "look_at_screen",
            "Take a screenshot of the focused monitor and answer a question about it.",
            {"question": text_param("What to find out from the screen")},
            look_at_screen,
        ),
        Tool(
            "run_command",
            "Run a bash command on the user's computer and return its output.",
            {"command": text_param("The bash command")},
            run_command,
            "Run this command?",
            preview_command,
        ),
    )
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": {"type": "object", "properties": tool.properties, "required": [k for k in tool.properties if k not in tool.optional]},
        },
    }
    for tool in TOOLS.values()
]
