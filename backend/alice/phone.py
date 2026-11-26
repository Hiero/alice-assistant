"""The phone companion: a TCP listener on the Tailscale address, and the tools she uses when you talk from the phone.

The phone speaks the same JSON lines as the shell. Its first line must be
{"type": "hello", "token": "...", "apps": [{"label": "Chrome", "package": "com.android.chrome"}, ...]}
and the server answers {"type": "hello_ok", "history": [{"role": "user" or "assistant", "text": "...", "phone": false}, ...],
"context": [...the same, with her mood tags, for her phone model...], "memories": "- likes tea\n- ...",
"outfit": {"outfit": "queen" or "", "faces": false, "hat": false, "worn": ..., "birthdays": [...], "greetings": {...}},
"settings": {"steam_id": "7656...", "steam_country": "br", "news_edition": "us"}}
(the latest turns of her one conversation, and what she remembers about the user) or closes the connection.
The phone then sends {"type": "phone_sync", "turns": [{"role": ..., "text": ..., "time": ms}]} with what was said
on it while the PC was out of reach, and gets {"type": "sync_ok", "added": n, "history": [...]}.
Phone-only server events: {"type": "phone_action", "action": "open_app", "package": "..."}
                          {"type": "phone_action", "action": "search", "query": "..."}
                          {"type": "audio", "rate": 24000, "fps": 30, "levels": [...], "pcm": "<base64 s16le mono>"}
                          (one spoken sentence, when the phone sent its user_message with "voice": true)
Phone-only client message: {"type": "voice_message", "pcm": "<base64 s16le mono 16 kHz>", "voice": true}
                          (something said into the phone; answered with a "transcript" event, then as a typed message)
"""

import asyncio
import base64
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from alice.debuglog import log
from alice.voice import MOUTH_FPS, REPLY_LANGUAGE, SPEECH_RATE, guess_language, mouth_levels, speakable, synthesize
from alice.tools import TOOLS, Action, Tool, group, text_param

PORT = int(os.environ.get("ALICE_PHONE_PORT", "7720"))
CONFIG_HOME = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
TOKEN_FILE = CONFIG_HOME / "alice/phone-token"
HELLO_TIMEOUT_SECONDS = 10
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_VOICE_SECONDS = 30
CONTEXT_MESSAGES = 8
RETRY_SECONDS = 30

# PC tools that act on the desktop, which the phone can't use. They stay in her tool list, since she remembers
# using them, but answer that they're computer-only; hiding them made her invent vague failures.
DESKTOP_ONLY = {"apps", "windows", "alarms", "music", "volume", "clipboard", "files", "look_at_screen", "run_command"}

PROMPT_NOTE = """
Right now the user is talking to you from their phone. The apps tool and open_search act on the phone. \
Music, alarms, windows, the clipboard, files, the screen and commands only work at the computer. \
Everything else works from the phone exactly as usual: weather, news, web search, Steam, movies, notes, memory, \
and the system tool, which reads their computer's CPU, GPU, memory, disks and temperatures remotely. \
So call system whenever they ask how their computer is doing, even right after something that only works at the computer."""


@dataclass
class PhoneSession:
    """What the phone told us when it connected, and how to send it actions."""

    apps: list[dict] = field(default_factory=list)
    send: object = None


class PhoneSpeaker:
    """Speaks a reply on the phone, in the same voice as on the PC: each sentence is made here, as soon as it
    is complete, and sent to the phone with its loudness for her lip sync. Same interface as voice.Speaker."""

    def __init__(self, send) -> None:
        self._send = send
        self._buffer = ""
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        self.draining = False
        self.language = "en"
        self._worker = asyncio.create_task(self._run())

    def feed(self, text: str) -> None:
        self._buffer += text
        *sentences, self._buffer = re.split(r"(?<=[.!?…:])\s+|\n+", self._buffer)
        for sentence in sentences:
            self._queue.put_nowait(sentence)

    async def finish(self) -> None:
        """Waits until every sentence has been sent; the phone plays them at its own pace."""
        self.draining = True
        self._queue.put_nowait(self._buffer)
        self._buffer = ""
        self._queue.put_nowait(None)
        await asyncio.gather(self._worker, return_exceptions=True)

    def stop(self) -> None:
        self._worker.cancel()

    async def _run(self) -> None:
        try:
            while (sentence := await self._queue.get()) is not None:
                text = speakable(sentence)
                if not re.search(r"\w", text):
                    continue
                self.language = guess_language(text, self.language) if REPLY_LANGUAGE == "auto" else REPLY_LANGUAGE
                audio = await asyncio.to_thread(synthesize, text, self.language)
                await self._send({
                    "type": "audio",
                    "rate": SPEECH_RATE,
                    "fps": MOUTH_FPS,
                    "levels": mouth_levels(audio),
                    "pcm": base64.b64encode(audio).decode(),
                })
        except ConnectionError:
            pass
        except (OSError, RuntimeError, ValueError) as error:
            print(f"phone speech failed: {error}", flush=True)


def load_token() -> str:
    """The shared secret the phone must send first. Made once, readable only by you."""
    if token := os.environ.get("ALICE_PHONE_TOKEN", "").strip():
        return token
    try:
        return TOKEN_FILE.read_text().strip()
    except FileNotFoundError:
        TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        fd = os.open(TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(token + "\n")
        return token


def clean_apps(raw) -> list[dict]:
    if not isinstance(raw, list):
        return []
    apps = []
    for app in raw[:2000]:
        if isinstance(app, dict) and isinstance(app.get("label"), str) and isinstance(app.get("package"), str):
            apps.append({"label": app["label"][:100], "package": app["package"][:200]})
    return apps


def phone_tools(session: PhoneSession) -> dict[str, Tool]:
    """The PC's tools minus the desktop-only ones, plus the phone's own apps and search."""

    async def search_apps(query: str = "") -> str:
        words = query.lower().split()
        found = [a for a in session.apps if all(w in f"{a['label']} {a['package']}".lower() for w in words)]
        if not found:
            return f"No phone app matches {query!r}." if query else "No apps were reported by the phone."
        return "\n".join(f"{a['label']} (app_id: {a['package']})" for a in found[:30])

    async def open_app(app_id: str) -> str:
        app = next((a for a in session.apps if a["package"] == app_id), None)
        if app is None:
            raise ValueError(f"No phone app has the app_id {app_id!r}. Search first.")
        await session.send({"type": "phone_action", "action": "open_app", "package": app_id})
        return f"Opened {app['label']} on the phone."

    async def open_search(query: str) -> str:
        await session.send({"type": "phone_action", "action": "search", "query": query})
        return f"Opened a web search for {query!r} on the phone. The user reads the results themselves."

    own = (
        group(
            "apps",
            "Apps installed on the user's phone. search: find apps by name and get their app_id. open: start one by its "
            "app_id from search; always search first, since an app_id is rarely just its name.",
            {"search": Action(search_apps, takes=("query",)), "open": Action(open_app, ("app_id",))},
            {"query": text_param("For search: text to look for in app names"), "app_id": text_param("For open: the app_id from search")},
        ),
        Tool(
            "open_search",
            "Open a web search in the phone's browser, when the user asks you to search or look something up for them to see.",
            {"query": text_param("Search terms")},
            open_search,
        ),
    )
    def computer_only(tool: Tool) -> Tool:
        async def unavailable(**_) -> str:
            return f"Not done: {tool.name} only works at the computer, and the user is on their phone. Tell them it has to wait until they're back at the computer."
        return Tool(tool.name, tool.description, tool.properties, unavailable, optional=tool.optional)

    tools = {name: computer_only(tool) if name in DESKTOP_ONLY else tool for name, tool in TOOLS.items()}
    return {**tools, **{tool.name: tool for tool in own}}


def schemas(tools: dict[str, Tool]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": {"type": "object", "properties": tool.properties, "required": [k for k in tool.properties if k not in tool.optional]},
            },
        }
        for tool in tools.values()
    ]


async def tailscale_address() -> str | None:
    if host := os.environ.get("ALICE_PHONE_HOST", "").strip():
        return host
    try:
        process = await asyncio.create_subprocess_exec(
            "tailscale", "ip", "-4", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        out, _ = await asyncio.wait_for(process.communicate(), 5)
    except (OSError, TimeoutError):
        return None
    lines = out.decode().split()
    return lines[0] if process.returncode == 0 and lines else None


async def serve_phone(handle_client, recent_history, memories, dress, settings) -> None:
    """Listens only on the Tailscale address, so the phone reaches it from anywhere and nothing else can.
    Tailscale may come up after the backend, so it keeps trying."""
    token = load_token()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        try:
            line = await asyncio.wait_for(reader.readline(), HELLO_TIMEOUT_SECONDS)
            hello = json.loads(line)
            given = str(hello.get("token", "")) if isinstance(hello, dict) else ""
            if hello.get("type") != "hello" or not hmac.compare_digest(given.encode(), token.encode()):
                log.debug("phone connection from %s refused", peer)
                writer.close()
                return
        except (TimeoutError, ValueError, ConnectionError, AttributeError):
            writer.close()
            return
        session = PhoneSession(apps=clean_apps(hello.get("apps")))
        log.debug("phone connected from %s with %d apps", peer, len(session.apps))
        hello_ok = {
            "type": "hello_ok",
            "history": recent_history(),
            # For her model on the phone: the last few turns and her memories, so she isn't a stranger offline.
            "context": recent_history(CONTEXT_MESSAGES, keep_moods=True),
            "memories": memories(),
            "outfit": dress(),
            "settings": settings(),
        }
        writer.write(json.dumps(hello_ok, ensure_ascii=False).encode() + b"\n")
        await writer.drain()
        await handle_client(reader, writer, phone=session)

    while True:
        host = await tailscale_address()
        if host:
            try:
                # Room for a voice message: 30 s of 16 kHz speech is about 1.3 MB once in base64.
                server = await asyncio.start_server(handle, host=host, port=PORT, limit=MAX_LINE_BYTES)
            except OSError as error:
                log.debug("phone listener on %s:%d failed: %s", host, PORT, error)
            else:
                print(f"phone listener on {host}:{PORT}", flush=True)
                async with server:
                    await server.serve_forever()
        await asyncio.sleep(RETRY_SECONDS)
