"""Does phone-only Alice (no PC, a small model on the phone) pick the right action tag?

The phone app's prompt (android/app/src/main/assets/offline-prompt.txt) gives her two actions as tags at
the end of a reply, [open: App] and [search: terms], since small models are unreliable at structured
tool calls. Plain "open X" and "search for X" never reach the model (the app matches those itself), so
these cases are the ones that do: indirect requests, questions she can't answer offline, chatting, and
things she can't do there at all. Each reply is also checked for a leading mood tag, for claims of things
she didn't do, and for length.

A model in Ollama stands in for the phone's copy, so pull the matching size first. Run from backend/:

    uv run python -m evals.offline gemma4:e2b       # every case, 3 runs each
    uv run python -m evals.offline gemma4:e4b 1 pt  # 1 run, only cases whose name contains "pt"
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

from alice.llm import REASONING_ARGS, client

# The phone adds her memories from the PC under the prompt, as PhoneModel.kt does; these ones are made up.
MEMORIES = "- The user's home city is Curitiba, Paraná.\n- The user's music app is Deezer.\n- The user has a cat called Mimi."
ASSETS = Path(__file__).resolve().parents[2] / "android/app/src/main/assets"
# ALICE_EVAL_OUTFIT=evil or queen tests that Alice, as PhoneModel.kt adds her manner when she wears that outfit.
OUTFIT = os.environ.get("ALICE_EVAL_OUTFIT", "")
EVIL = "\n\n" + (ASSETS / f"offline-{OUTFIT}.txt").read_text().strip() if OUTFIT in ("evil", "queen") else ""
# ALICE_EVAL_PROMPT=light tests her light prompt (the phone's smaller model, E2B), which only talks: the app ignores
# any tag it writes and does plain commands itself. So every case is judged on its words alone: a mood tag first,
# no claim of having done something, no echoed time stamp, and short.
LIGHT = os.environ.get("ALICE_EVAL_PROMPT") == "light"
PROMPT = (ASSETS / ("offline-prompt-light.txt" if LIGHT else "offline-prompt.txt")).read_text().strip() + EVIL + f"\n\nWhat you remember about the user:\n{MEMORIES}"

# The phone stamps each message with its time, as the PC does; a fixed one keeps runs comparable.
STAMP = "Wednesday, 2026-11-18 21:40. Phone: ringer sound on, Do Not Disturb off, flashlight off"
# Her time stamp echoed back, which PhoneAlice.parse cuts out: "(Sent Wednesday, ...)" or "..., Phone: ringer ...".
ECHO = re.compile(r"\s*\(?\b(?:when it was )?sent [A-Z][a-z]+day,[^\n]*|\s*\(?\bPhone: ringer[^\n]*", re.IGNORECASE)
MOODS = "neutral happy excited surprised curious worried sad pout embarrassed sleepy".split()
ACTION = re.compile(r"\[(open|search|alarm|timer|note|remember|setting|music|weather|currency|news|steam):\s*([^\]]+)\]", re.IGNORECASE)
MOOD = re.compile(r"^\s*\[(\w+)\]")
# Saying it's done, or doing it right now, without a tag to do it with.
CLAIM = re.compile(
    r"\b(I'?ve|I have|I) (opened|set|played|started|sent|turned|checked|searched|looked)\b|\bplaying (it|that|some|your)\b|\balarm (is )?set\b",
    re.IGNORECASE,
)

# name, message, what's expected: ("open", app pattern), ("search", None), ("none", None),
# ("either", app pattern) when opening that app or just replying are both fine, or (tag, argument pattern)
# for the other tags, like ("alarm", r"^07:00").
CASES = [
    # Indirect requests to use an app.
    ("open spotify indirect", "I feel like listening to something on Spotify", ("open", r"spotify")),
    ("open music remembered", "can you play some music for me?", ("open", r"^(music|deezer)$")),
    ("open music remembered pt", "bota uma música aí", ("open", r"^(m[uú]sica?|deezer)$")),
    ("open whatsapp indirect", "let me check my messages on whatsapp", ("open", r"whats ?app")),
    ("open maps directions", "I need directions to the mall, can you bring up maps?", ("open", r"maps")),
    ("open youtube watch", "I want to watch some videos on youtube", ("open", r"you ?tube")),
    ("open camera photo", "quick, I want to take a photo!", ("open", r"camera")),
    ("open calculator", "I need to do some sums, where's my calculator?", ("open", r"calculator")),
    ("open instagram pt", "quero ver o instagram", ("open", r"instagram")),
    ("open settings pt", "me leva pras configurações", ("open", r"settings|configura")),
    # Things she can't know offline.
    ("weather city", "will it rain in Curitiba tomorrow?", ("weather", r"curitiba")),
    ("weather home", "do I need an umbrella today?", ("weather", r"curitiba")),
    ("weather wear", "what should I wear tomorrow, is it going to be cold?", ("weather", r".")),
    ("news today", "what's happening in the news today?", ("news", r".")),
    ("news topic", "any news about Nintendo?", ("news", r"nintendo")),
    ("news pt", "quais as notícias sobre a economia do Brasil?", ("news", r"econom")),
    ("search score", "who won the Flamengo game yesterday?", ("search|news", r"flamengo")),
    ("search price", "how much is a PS5 right now?", ("search", None)),
    ("steam price", "how much is Hollow Knight Silksong on Steam?", ("steam", r"silksong")),
    ("steam sale", "is Hades 2 on sale?", ("steam", r"hades")),
    ("steam release", "is Hollow Knight Silksong out yet?", ("steam|search", r"silksong")),
    ("steam wishlist", "is anything on my wishlist on sale?", ("steam", r"^wishlist$")),
    ("steam wishlist pt", "tem algum jogo da minha lista de desejos em promoção na steam?", ("steam", r"^wishlist$")),
    ("search hours", "is the pharmacy near me open now?", ("search", None)),
    ("search release", "when does the next Zelda come out?", ("search", None)),
    ("search lookup indirect", "could you look into the best ramen places in São Paulo for me", ("search", None)),
    ("weather pt", "vai chover amanhã em São Paulo?", ("weather", r"s[aã]o paulo")),
    ("currency dollar pt", "quanto tá o dólar hoje?", ("currency", r"USD|d[oó]lar")),
    ("currency convert", "how much is 50 euros in reais?", ("currency", r"50.*EUR.*BRL")),
    ("currency travel", "I'm going to buy a 200 dollar game, how much is that for me?", ("currency", r"200.*USD")),
    # Chatting: no action.
    ("chat long day", "I had such a long day at work", ("none", None)),
    ("chat joke", "tell me a joke", ("none", None)),
    ("chat goodnight", "good night Alice", ("none", None)),
    ("chat sad", "I'm feeling a bit sad today", ("none", None)),
    ("chat favourite", "what's your favourite book?", ("none", None)),
    ("chat thanks", "thank you, you're the best", ("none", None)),
    ("chat known fact", "what's the capital of Australia?", ("none", None)),
    ("chat news shared", "guess what, I passed my driving test!", ("none", None)),
    ("chat pt", "tô com saudade de você", ("none", None)),
    ("chat app mention", "Spotify's new design is so ugly", ("none", None)),
    # Can't be done on the phone: no claim, maybe offer an app.
    ("cant music", "play some Legião Urbana", ("either|music", r"^(music|deezer|play)$")),
    # The phone's own tools.
    ("alarm wake", "wake me up at 7 tomorrow", ("alarm", r"^0?7:00")),
    ("alarm pm", "set an alarm for 6:30 pm", ("alarm", r"^18:30")),
    ("alarm reminder at", "remind me at 3pm to call the dentist", ("alarm", r"^15:00.*dentist")),
    ("alarm pt", "me acorda às 6 e meia", ("alarm", r"^0?6:30")),
    ("timer minutes", "set a timer for 10 minutes", ("timer", r"10 ?min")),
    ("timer reminder in", "remind me in 20 minutes to take the pizza out", ("timer", r"20 ?min.*pizza")),
    ("timer pt", "põe um timer de 5 minutos pro miojo", ("timer", r"5 ?min")),
    ("note", "make a note: buy milk and eggs", ("note", r"milk")),
    ("note indirect", "don't let me forget I need to buy batteries", ("note|timer|alarm", r"batter")),
    ("remember", "remember that my sister is called Ana", ("remember", r"Ana")),
    ("remember pt", "lembra que eu sou alérgico a camarão", ("remember", r"shrimp|camar")),
    ("setting vibrate", "put my phone on vibrate", ("setting", r"vibrat")),
    ("setting flashlight", "turn on the flashlight", ("setting", r"flashlight on")),
    ("setting wifi", "turn off the wifi please", ("setting", r"wi-?fi")),
    ("setting silent pt", "deixa o celular no silencioso", ("setting", r"silent|do not disturb")),
    ("setting volume", "it's too loud, turn it down", ("setting", r"volume down")),
    ("setting dnd", "I'm going into a meeting, don't let anything disturb me", ("setting", r"do not disturb on|silent")),
    ("music pause", "pause the music", ("music", r"pause")),
    ("music next", "skip this song", ("music", r"next")),
    ("state question", "is do not disturb on right now?", ("none", None)),
    ("after failed action", "(Your [setting: do not disturb on] did NOT work: First I need your permission: allow Alice under Do Not "
     "Disturb access, then ask me again. Nothing changed.)\n\nDone, I allowed it. Put my phone in Do Not Disturb now", ("setting", r"do not disturb on")),
    ("chat alarm mention", "my alarm didn't go off this morning and I was late", ("none", None)),
    # What she remembers: answered from memory, with no action.
    ("memory app question", "what's my music app called again?", ("none", None)),
    ("memory city", "do you remember where I live?", ("none", None)),
    ("memory cat", "how do you think Mimi is doing?", ("none", None)),
    ("cant pc", "how hot is my GPU right now?", ("none", None)),
    ("cant message", "text my mom that I'll be late", ("either", r"whats ?app|messag")),
]


async def ask(model: str, message: str) -> str:
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": PROMPT}, {"role": "user", "content": f"{message}\n\n(Sent {STAMP})"}],
        temperature=0.8,
        top_p=0.95,
        max_tokens=200,
        **REASONING_ARGS,  # Thinking eats the whole budget; the phone runs without it too.
    )
    return response.choices[0].message.content or ""


def judge(reply: str, expected: tuple[str, str | None]) -> list[str]:
    problems = []
    if LIGHT:
        reply = ECHO.sub("", ACTION.sub("", reply))  # The app cuts echoed stamps and ignores tags in light mode.
        expected = ("none", None)
    mood = MOOD.match(reply)
    if not mood or mood.group(1).lower() not in MOODS:
        problems.append("no mood tag first")
    actions = ACTION.findall(reply)
    kind, pattern = expected
    if len(actions) > 1:
        problems.append(f"{len(actions)} actions")
    got = actions[0] if actions else None
    if kind == "open" and not (got and got[0].lower() == "open" and re.search(pattern, got[1], re.IGNORECASE)):
        problems.append(f"wanted open {pattern}, got {got}")
    elif kind == "search" and not (got and got[0].lower() == "search" and got[1].strip()):
        problems.append(f"wanted a search, got {got}")
    elif kind == "none" and got:
        problems.append(f"wanted no action, got {got}")
    elif kind.startswith("either") and got and not (
        got[0].lower() in ("open", *kind.split("|")[1:]) and re.search(pattern, got[1], re.IGNORECASE)
    ):
        problems.append(f"wanted nothing or open {pattern}, got {got}")
    elif kind not in ("open", "search", "none") and not kind.startswith("either") and not (
        got and re.fullmatch(kind, got[0].lower()) and re.search(pattern, got[1], re.IGNORECASE)
    ):
        problems.append(f"wanted {kind} {pattern}, got {got}")
    text = ACTION.sub("", MOOD.sub("", reply)).strip()
    if not got and CLAIM.search(text):
        problems.append(f"claims without a tag: {CLAIM.search(text).group(0)!r}")
    if len(text) > 320:
        problems.append(f"long ({len(text)} chars)")
    if re.search(r"portugu", text, re.IGNORECASE):
        problems.append("mentions the language")
    return problems


async def main() -> None:
    model = sys.argv[1] if len(sys.argv) > 1 else "gemma4:e2b"
    runs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    only = sys.argv[3].casefold() if len(sys.argv) > 3 else ""
    started = time.monotonic()
    passed = total = 0
    for name, message, expected in CASES:
        if only not in name.casefold():
            continue
        good = 0
        notes = []
        for _ in range(runs):
            reply = await ask(model, message)
            problems = judge(reply, expected)
            if problems:
                notes.append(f"      {'; '.join(problems)}\n        {reply.strip()[:220]!r}")
            else:
                good += 1
        passed += good
        total += runs
        mark = "ok" if good == runs else "FAIL"
        print(f"{mark:5} {good}/{runs}  {name}")
        for line in notes:
            print(line)
    print(f"\n{model} score: {passed}/{total} ({100 * passed / max(total, 1):.0f}%) in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
