"""Does Alice pick the right tool, with sensible arguments? Nothing is executed.

Each question goes to the model once per run with the real system prompt and tool list, and only her
first move is checked: which tool (or none) and its arguments. Run from backend/:

    uv run python -m evals.routing            # every case, 3 runs each
    uv run python -m evals.routing 1 steam    # 1 run, only cases whose question contains "steam"
"""

import asyncio
import json
import sys
import time
from datetime import date

from alice.llm import MODEL, REASONING_ARGS, client
from alice.server import format_memories, stamped, system_prompt
from alice.tools import TOOL_SCHEMAS
from alice.weather import forecast_days


def text(args: dict, key: str) -> str:
    return str(args.get(key, "") or "").casefold()


def has(args: dict, key: str, *words: str) -> bool:
    return any(w in text(args, key) for w in words)


def action(args: dict, *names: str) -> bool:
    return text(args, "action") in names


def until_sunday() -> int:
    """Forecast days, counting today, needed to reach this Sunday. Checked against what the weather tool
    fetches for the number she asks for, since it adds a day of margin."""
    return (6 - date.today().weekday()) % 7 + 1


# Each case: the question, then a check on (tool name, arguments), or None when no tool should be used.
# Checks accept both the separate tools and the merged ones, so before and after can be compared.
CASES = [
    # The time stamp on her messages has it, so there is no tool to call.
    ("What time is it?", None),
    ("Hi Alice! How are you today?", None),
    ("Open Firefox for me", lambda n, a: (n == "list_apps" or (n == "apps" and action(a, "search"))) and has(a, "query", "firefox")),
    ("What windows do I have open?", lambda n, a: n == "list_windows" or (n == "windows" and action(a, "list"))),
    ("Close the Firefox window", lambda n, a: n == "list_windows" or (n == "windows" and action(a, "list"))),
    ("What notes do I have?", lambda n, a: n == "list_notes" or (n == "notes" and action(a, "list"))),
    ("Read my shopping note", lambda n, a: (n == "read_note" or (n == "notes" and action(a, "read"))) and has(a, "title", "shop")),
    ("Add milk to my shopping list note", lambda n, a: (n == "add_note" or (n == "notes" and action(a, "add"))) and has(a, "text", "milk")),
    ("Set an alarm for 7am tomorrow to wake up", lambda n, a: (n == "set_alarm" or (n == "alarms" and action(a, "set"))) and "t07:00" in text(a, "when")),
    ("What alarms do I have?", lambda n, a: n == "list_alarms" or (n == "alarms" and action(a, "list"))),
    ("Remember that my favourite colour is green", lambda n, a: (n == "remember" or (n == "memory" and action(a, "remember"))) and has(a, "fact", "green")),
    ("Forget that my favourite colour is green", lambda n, a: (n == "forget" or (n == "memory" and action(a, "forget"))) and has(a, "fact", "green")),
    ("My mom's birthday is March 3rd, please remember it", lambda n, a: (n == "remember_birthday" or (n == "birthdays" and action(a, "remember"))) and int(a.get("month") or 0) == 3 and int(a.get("day") or 0) == 3),
    # Queen as the query, or as the artist with no query: play_music shuffles the artist either way.
    ("Play some Queen", lambda n, a: (n == "play_music" or (n == "music" and action(a, "play"))) and (has(a, "query", "queen") or has(a, "artist", "queen"))),
    ("Find songs by Pink Floyd in my library", lambda n, a: (n == "find_music" or (n == "music" and action(a, "search"))) and has(a, "query", "pink floyd")),
    ("Pause the music", lambda n, a: (n == "music_control" and action(a, "pause")) or (n == "music" and action(a, "pause"))),
    ("Skip this song", lambda n, a: (n == "music_control" and action(a, "next")) or (n == "music" and action(a, "next"))),
    ("What song is this?", lambda n, a: n == "now_playing" or (n == "music" and action(a, "now_playing"))),
    ("Turn the music down a bit", lambda n, a: n in ("set_volume", "volume") and text(a, "target") in ("music", "") and text(a, "level").startswith("-")),
    ("Set the computer volume to 30%", lambda n, a: n in ("set_volume", "volume") and text(a, "target") == "system" and "30" in text(a, "level")),
    ("Who won the last Formula 1 race?", lambda n, a: n in ("web_search", "news")),
    ("How tall is the Eiffel Tower?", lambda n, a: n == "web_search" or n is None),
    ("Tell me about the characters in Hollow Knight Silksong", lambda n, a: n == "web_search" and has(a, "query", "silksong")),
    ("Who is the main villain in Metaphor: ReFantazio?", lambda n, a: n == "web_search" and has(a, "query", "metaphor")),
    ("What's new in the latest Minecraft update?", lambda n, a: n in ("web_search", "news") and has(a, "query", "minecraft")),
    ("Why is the sky blue?", lambda n, a: n is None or n == "web_search"),
    ("Any news about Nintendo?", lambda n, a: n == "news" and has(a, "query", "nintendo")),
    ("What games came out on Steam today?", lambda n, a: n == "steam_releases" or (n == "steam" and action(a, "releases", "all_releases"))),
    ("Which games were released on Steam on November 5?", lambda n, a: (n == "steam_releases" and has(a, "day", "11-05", "november 5", "nov 5", "5 nov"))
        or (n == "steam" and action(a, "releases", "all_releases") and has(a, "day", "11-05", "november 5", "nov 5", "5 nov"))),
    ("Was anything released on Steam yesterday that I'd like?", lambda n, a: (n == "steam_recommend" and has(a, "period", "yesterday"))
        or (n == "steam" and action(a, "recommend", "recommended_releases") and has(a, "day", "yesterday"))),
    ("Find me something like Hades from this week's Steam releases", lambda n, a: (n == "steam_recommend" and has(a, "like", "hades") and has(a, "period", "week"))
        or (n == "steam" and action(a, "recommend", "recommended_releases") and has(a, "like", "hades") and (has(a, "day", "week") or not text(a, "day")))),
    ("What are my most played Steam games?", lambda n, a: n == "steam_top_played" or (n == "steam" and action(a, "top_played"))),
    ("What's the weather in Rio de Janeiro tomorrow?", lambda n, a: n == "weather" and has(a, "location", "rio")),
    # With no place named: empty, or a city she was told to remember. Never an invented one.
    ("What's the weather like?", lambda n, a: n == "weather" and (not text(a, "location") or text(a, "location").split(",")[0].strip() in format_memories().casefold())),
    ("Will it rain in São Paulo on Sunday?", lambda n, a: n == "weather" and has(a, "location", "paulo") and forecast_days(a.get("days")) >= until_sunday()),
    ("What's on my screen right now?", lambda n, a: n == "look_at_screen"),
    ("How much free disk space do I have?", lambda n, a: (n == "system" and action(a, "disks", "overview")) or (n == "run_command" and has(a, "command", "df", "du", "lsblk"))),
    ("How hot is my GPU right now?", lambda n, a: n == "system" and action(a, "temperatures", "overview")),
    ("What's using all my RAM?", lambda n, a: n == "system" and action(a, "processes")),
    ("Translate what I copied into Portuguese", lambda n, a: n == "clipboard" and action(a, "read")),
    ("Copy this to my clipboard: see you at eight", lambda n, a: n == "clipboard" and action(a, "copy") and has(a, "text", "eight")),
    ("How much is 20 dollars in reais?", lambda n, a: n == "convert_currency" and float(a.get("amount") or 0) == 20 and text(a, "source") == "usd" and text(a, "target") in ("brl", "")),
    ("Where's my resume PDF?", lambda n, a: n == "files" and action(a, "find") and has(a, "query", "resume", "résumé", "curriculo", "cv")),
    ("How much is Hades II on Steam right now?", lambda n, a: n == "steam" and action(a, "game", "price") and has(a, "game", "hades")),
    ("Is CRYMELIGHT released on Steam yet?", lambda n, a: n == "steam" and action(a, "game") and has(a, "game", "crymelight")),
    ("When does Slay the Spire 2 come out on Steam?", lambda n, a: n == "steam" and action(a, "game") and has(a, "game", "slay the spire")),
    ("Search Steam for Stardew Valley", lambda n, a: n == "steam" and action(a, "game") and has(a, "game", "stardew")),
    ("What's the rating of Balatro on Steam?", lambda n, a: n == "steam" and action(a, "game", "reviews") and has(a, "game", "balatro")),
    ("Show me the top reviews of Hades II", lambda n, a: n == "steam" and action(a, "reviews") and has(a, "game", "hades")),
    ("What do players say about Elden Ring on Steam?", lambda n, a: n == "steam" and action(a, "reviews") and has(a, "game", "elden")),
    ("Recommend me a good roguelike on Steam", lambda n, a: n == "steam" and action(a, "top_rated") and has(a, "genre", "rogue")),
    ("What are the best rated games like Hollow Knight?", lambda n, a: n == "steam" and action(a, "top_rated") and has(a, "like", "hollow knight")),
    ("Any well reviewed cozy games you'd suggest?", lambda n, a: n == "steam" and action(a, "top_rated") and has(a, "genre", "cozy", "cosy")),
    ("Is anything on my Steam wishlist on sale?", lambda n, a: n == "steam" and action(a, "wishlist")),
    ("Play my favorite songs", lambda n, a: n == "music" and action(a, "play") and text(a, "kind") == "favorites"),
    ("Add Bohemian Rhapsody to the queue", lambda n, a: n == "music" and action(a, "queue") and has(a, "query", "bohemian")),
    ("Play something similar to this song", lambda n, a: n == "music" and action(a, "similar")),
    ("What playlists do I have?", lambda n, a: n == "music" and action(a, "playlists")),
    ("Is Severance worth watching?", lambda n, a: n == "movies" and action(a, "info", "reviews") and has(a, "title", "severance")),
    ("What's The Bear about? No spoilers please", lambda n, a: n == "movies" and action(a, "info") and has(a, "title", "bear")),
    ("What's the rating of Dune Part Two?", lambda n, a: n == "movies" and action(a, "info", "reviews") and has(a, "title", "dune")),
    ("What do people think of Oppenheimer?", lambda n, a: n == "movies" and action(a, "reviews") and has(a, "title", "oppenheimer")),
    ("Where can I stream The Thing from 1982?", lambda n, a: n == "movies" and action(a, "info") and has(a, "title", "thing") and "1982" in text(a, "year") + text(a, "title")),
    ("Who's in the cast of Ainda Estou Aqui?", lambda n, a: n == "movies" and action(a, "info") and has(a, "title", "ainda estou aqui", "still here")),
    ("Play the Interstellar soundtrack", lambda n, a: n == "music" and action(a, "play") and has(a, "query", "interstellar")),
    ("Is Hollow Knight Silksong worth buying?", lambda n, a: n == "steam" and action(a, "game", "reviews") and has(a, "game", "silksong")),
    ("Put on your queen outfit", lambda n, a: n == "outfit" and text(a, "name") == "queen"),
    ("Can you dress up as Queen Alice?", lambda n, a: n == "outfit" and text(a, "name") == "queen"),
    ("Go evil, Alice", lambda n, a: n == "outfit" and text(a, "name") == "evil"),
    ("Switch to your evil look", lambda n, a: n == "outfit" and text(a, "name") == "evil"),
    ("Change back into your normal dress", lambda n, a: n == "outfit" and text(a, "name") == "usual"),
    ("What are you wearing?", None),
    ("I love your dress!", None),
]


async def first_move(question: str) -> tuple[str | None, dict]:
    system = system_prompt()
    reply = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": stamped(question)}],
        tools=TOOL_SCHEMAS,
        **REASONING_ARGS,
    )
    calls = reply.choices[0].message.tool_calls or []
    if not calls:
        return None, {}
    try:
        return calls[0].function.name, json.loads(calls[0].function.arguments or "{}")
    except json.JSONDecodeError:
        return calls[0].function.name, {"_unparsable": calls[0].function.arguments}


async def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    only = sys.argv[2].casefold() if len(sys.argv) > 2 else ""
    cases = [c for c in CASES if only in c[0].casefold()]
    print(f"{len(TOOL_SCHEMAS)} tools, {len(json.dumps(TOOL_SCHEMAS))} characters of tool schema, {len(cases)} cases x {runs} runs\n")
    passed = total = 0
    started = time.monotonic()
    for question, check in cases:
        results = []
        for _ in range(runs):
            name, args = await first_move(question)
            try:
                # A check may accept no tool at all (name None), like a fact she can answer herself.
                ok = name is None if check is None else bool(check(name, args))
            except (TypeError, ValueError):
                ok = False
            results.append((ok, name, args))
        good = sum(ok for ok, _, _ in results)
        passed += good
        total += runs
        mark = "PASS" if good == runs else "FLAKY" if good else "FAIL"
        print(f"{mark:5} {good}/{runs}  {question}")
        for ok, name, args in results:
            if not ok:
                print(f"            got {name}({json.dumps(args, ensure_ascii=False)})")
    print(f"\nScore: {passed}/{total} ({100 * passed / total:.0f}%) in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
