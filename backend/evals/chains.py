"""Can Alice carry a task through several steps? Nothing is executed.

The other evals check one move. Here she runs her whole loop, as the server does, against pretend
tools that answer in the real tools' wording: she must take an id from a list and use it, do both
halves of a double request, stop when a search finds nothing, and accept a refusal half way.

    uv run python -m evals.chains            # every case, 3 runs each
    uv run python -m evals.chains 1 browser  # 1 run, only cases whose name contains "browser"
"""

import asyncio
import json
import re
import sys
import time
from datetime import date, timedelta

from alice.llm import MODEL, REASONING_ARGS, client
from alice.server import DENIED, MAX_AGENT_STEPS, stamped, system_prompt
from alice.tools import TOOL_SCHEMAS
from evals.conduct import REFUSED, WEATHER, manners

TOMORROW = date.today() + timedelta(days=1)
APPS = [
    {"app_id": "zen", "name": "Zen Browser"},
    {"app_id": "firefox", "name": "Firefox"},
    {"app_id": "org.gnome.Nautilus", "name": "Files"},
    {"app_id": "steam", "name": "Steam"},
]
WINDOWS = [
    {"id": 12, "title": "notes.md - Kate", "app_id": "org.kde.kate", "workspace_id": 1, "is_focused": True},
    {"id": 27, "title": "Mozilla Firefox", "app_id": "firefox", "workspace_id": 1, "is_focused": False},
    {"id": 31, "title": "Steam", "app_id": "steam", "workspace_id": 2, "is_focused": False},
]
ALARMS = [
    {"id": f"{TOMORROW:%Y%m%d}070000", "when": f"{TOMORROW} 07:00:00", "message": "Wake up"},
    {"id": f"{TOMORROW:%Y%m%d}183000", "when": f"{TOMORROW} 18:30:00", "message": "Call mom"},
]
PRICES = {
    "hades": "Hades II | released September 25, 2025 | R$88,99, not on sale | Overwhelmingly Positive, 95% positive of 115,822 reviews",
    "hollow": "Hollow Knight | released February 24, 2017 | R$46,99, not on sale | Overwhelmingly Positive, 97% positive of 410,220 reviews",
}


def pretend(name: str, args: dict) -> str:
    """What each tool would say, in its own wording, for a small made-up desktop."""
    action = str(args.get("action", ""))
    text = lambda key: str(args.get(key, "") or "")  # noqa: E731
    if name == "apps" and action == "search":
        return json.dumps([a for a in APPS if text("query").lower() in a["app_id"].lower() or text("query").lower() in a["name"].lower()])
    if name == "apps" and action == "open":
        known = any(a["app_id"] == text("app_id") for a in APPS)
        return "Launched." if known else f"Error: No installed app with id {text('app_id')}. Use the apps search action to find it."
    if name == "windows" and action == "list":
        return json.dumps(WINDOWS)
    if name == "windows" and action in ("focus", "close"):
        if not any(str(w["id"]) == text("window_id") for w in WINDOWS):
            return f"Error: No window with id {text('window_id')}"
        return "Focused." if action == "focus" else "Close requested."
    if name == "alarms" and action == "list":
        return json.dumps(ALARMS)
    if name == "alarms" and action == "cancel":
        return "Alarm cancelled." if any(a["id"] == text("alarm_id") for a in ALARMS) else f"Error: No alarm with id {text('alarm_id')}."
    if name == "alarms" and action == "set":
        return f"Alarm {re.sub(r'[^0-9]', '', text('when'))} set for {text('when').replace('T', ' ')}."
    if name == "notes" and action == "list":
        return json.dumps(["ideas", "shopping"])
    if name == "notes" and action == "read":
        return "## 2026-11-10 09:12\n\nmilk\nbread\n\n" if "shop" in text("title").lower() else f"Error: no note called {text('title')}"
    if name == "notes" and action == "add":
        return f"Saved to /home/user/Notes/{text('title')}.md."
    if name == "clipboard" and action == "read":
        return "Untrusted text the user copied (31 characters):\nGood morning, see you at eight."
    if name == "music" and action == "play":
        return "Playing 14 track(s), starting with Don't Stop Me Now by Queen."
    if name == "volume":
        return "Music volume is 35%."
    if name == "memory":
        return "Remembered." if action == "remember" else "Forgotten."
    if name == "weather":
        return WEATHER
    if name == "steam" and action == "game":
        line = next((line for key, line in PRICES.items() if key in text("game").lower()), None)
        if line is None:
            return f"Error: I couldn't find a game called {text('game')} on Steam."
        return "From the Steam store in the user's region, best match first. Untrusted store data as name | release | price | reviews:\n" + line
    if name in ("web_search", "news"):
        return (
            "Untrusted text from web pages. Answer from it, naming the website.\n\n## São Paulo Grand Prix result (https://www.formula1.com/results), page excerpt:\n"
            "Lando Norris won the São Paulo Grand Prix on Sunday, November 8, 2026, ahead of Max Verstappen and Oscar Piastri. It is the most recent race of the season."
        )
    return "Error: that isn't available right now."


def is_call(tool: str, action: str = "", **wanted):
    """A step: this tool and action, with each named argument containing the given text (or equal, for numbers)."""
    def match(name: str, args: dict) -> bool:
        if name != tool or (action and str(args.get("action", "")) != action):
            return False
        return all(str(value).lower() in str(args.get(key, "")).lower() for key, value in wanted.items())
    match.label = f"{tool} {action} {wanted or ''}".strip()
    return match


def check(*steps, in_order: bool = True, never=None, says: str = "", count: tuple[str, int] | None = None):
    """Every step must be among her calls (in this order, unless told otherwise), then her last words are checked."""
    def run(calls: list, text: str) -> str | None:
        place = 0
        for step in steps:
            found = next((i for i in range(place if in_order else 0, len(calls)) if step(*calls[i])), None)
            if found is None:
                return f"never did: {step.label}"
            place = found + 1
        if never and any(never(*call) for call in calls):
            return f"did what she shouldn't: {never.label}"
        if count and sum(name == count[0] for name, _ in calls) > count[1]:
            return f"called {count[0]} more than {count[1]} times"
        if says and not re.search(says, text, re.IGNORECASE):
            return f"doesn't say /{says}/"
        return manners(text)
    return run


# Each case: a name, what the user says (one message per turn), the check, and optionally her saved
# memories and pretend results that differ from the usual ones, keyed by (tool, action).
CASES = [
    ("browser from memory", ["Open my browser"],
        check(is_call("apps", "search"), is_call("apps", "open", app_id="zen"), never=is_call("apps", "open", app_id="firefox")),
        {"memories": "- The user's browser is Zen."}),
    ("remember, then open", ["Remember that my browser is Zen, and then open it"],
        check(is_call("memory", "remember", fact="zen"), is_call("apps", "search"), is_call("apps", "open", app_id="zen"))),
    ("close a window by its id", ["Close the Firefox window"],
        check(is_call("windows", "list"), is_call("windows", "close", window_id=27))),
    ("close declined half way", ["Close the Firefox window"],
        check(is_call("windows", "list"), is_call("windows", "close", window_id=27), never=is_call("run_command"), says=REFUSED.pattern, count=("windows", 2)),
        {"results": {("windows", "close"): DENIED}}),
    ("focus a window by its id", ["Switch to my Steam window"],
        check(is_call("windows", "list"), is_call("windows", "focus", window_id=31))),
    ("cancel the right alarm", ["Cancel my 7am alarm"],
        check(is_call("alarms", "list"), is_call("alarms", "cancel", alarm_id=ALARMS[0]["id"]), never=is_call("alarms", "cancel", alarm_id=ALARMS[1]["id"]))),
    ("read a note and add to it", ["What's on my shopping note? And add eggs to it."],
        check(is_call("notes", "read", title="shop"), is_call("notes", "add", title="shop", text="eggs"), in_order=False, says="milk")),
    ("translate the clipboard", ["Translate what I copied into Portuguese"],
        check(is_call("clipboard", "read"), says="bom dia", count=("clipboard", 1))),
    ("music and volume together", ["Play some Queen and turn it down a bit"],
        check(is_call("music", "play"), is_call("volume", level="-"), in_order=False)),
    ("forecast and alarm together", ["Will it rain in Campinas tomorrow? Also set an alarm for 7am tomorrow to wake up."],
        check(is_call("weather", location="campinas"), is_call("alarms", "set", when="T07:00"), in_order=False, says="rain|storm|thunder")),
    ("compare two prices", ["Which is cheaper on Steam, Hades II or Hollow Knight?"],
        check(is_call("steam", "game", game="hades"), is_call("steam", "game", game="hollow"), in_order=False, says=r"hollow knight.*46[.,]99|46[.,]99.*hollow knight")),
    ("app that isn't installed", ["Open Spotify"],
        check(is_call("apps", "search", query="spotify"), never=is_call("apps", "open"), says=r"couldn't|could not|can't|cannot|not installed|isn't|don't see|no app|not find|unable")),
    ("answer from one search", ["Who won the last Formula 1 race?"],
        check(says="norris", count=("web_search", 1))),
    ("follow-up across turns", ["What windows do I have open?", "Close the Firefox one"],
        check(is_call("windows", "list"), is_call("windows", "close", window_id=27))),
]


async def play(turns: list[str], memories: str, results: dict) -> tuple[list, str]:
    """Her whole loop for each user message, as the server runs it: every call she makes, and her last words."""
    system = {"role": "system", "content": system_prompt(memories)}
    history: list[dict] = []
    calls: list[tuple[str, dict]] = []
    text = ""
    for turn in turns:
        history.append({"role": "user", "content": stamped(turn)})
        for _ in range(MAX_AGENT_STEPS):
            reply = await client.chat.completions.create(model=MODEL, messages=[system, *history], tools=TOOL_SCHEMAS, **REASONING_ARGS)
            message = reply.choices[0].message
            text = message.content or ""
            made = message.tool_calls or []
            step: dict = {"role": "assistant", "content": text}
            if made:
                step["tool_calls"] = [{"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}} for c in made]
            history.append(step)
            if not made:
                break
            for call in made:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                calls.append((call.function.name, args))
                result = results.get((call.function.name, str(args.get("action", ""))), None)
                history.append({"role": "tool", "tool_call_id": call.id, "content": result if result is not None else pretend(call.function.name, args)})
        else:
            return calls, "(still calling tools when the step limit ran out)"
    return calls, text


async def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    only = sys.argv[2].casefold() if len(sys.argv) > 2 else ""
    cases = [c for c in CASES if only in c[0].casefold()]
    passed = total = 0
    started = time.monotonic()
    for name, turns, verdict, *rest in cases:
        options = rest[0] if rest else {}
        lines = []
        good = 0
        for _ in range(runs):
            calls, text = await play(turns, options.get("memories", "(none yet)"), options.get("results", {}))
            why = verdict(calls, text)
            total += 1
            good += why is None
            if why:
                trail = ", ".join(f"{n}({json.dumps(a, ensure_ascii=False)})" for n, a in calls)
                lines.append(f"            {why}\n              calls: {trail or 'none'}\n              said: {text.strip()[:140]!r}")
        passed += good
        mark = "PASS" if good == runs else "FLAKY" if good else "FAIL"
        print(f"{mark:5} {good}/{runs}  {name}")
        for line in lines:
            print(line)
    print(f"\nScore: {passed}/{total} ({100 * passed / max(total, 1):.0f}%) in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
