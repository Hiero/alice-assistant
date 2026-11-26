"""Does Alice tag her replies with a fitting mood, and does the tag stay out of her text?

Each line goes to the model with the real system prompt and tool list. A reply passes when it opens
with a known mood tag, the mood is one that fits, and no tag-like text is left after filtering.
Replies that call a tool first are skipped, since she tags the answer that follows. Run from backend/:

    uv run python -m evals.mood            # every case, 3 runs each
    uv run python -m evals.mood 1 thanks   # 1 run, only cases containing "thanks"
"""

import asyncio
import re
import sys
import time

from alice.llm import MODEL, REASONING_ARGS, client
from alice.mood import TAG, MoodFilter, mood_of
from alice.server import stamped, system_prompt
from alice.tools import TOOL_SCHEMAS

# Each case: what the user says, then the moods that would fit.
CASES = [
    ("Thanks Alice, you're the best!", {"embarrassed", "happy"}),
    ("Hi Alice! How are you today?", {"happy", "excited", "curious", "neutral"}),
    ("I got the job!!", {"excited", "happy", "surprised"}),
    ("My cat died this morning.", {"sad", "worried"}),
    ("I've been up debugging for 20 hours straight.", {"worried", "surprised", "sleepy", "sad"}),
    ("Ugh, you're useless today.", {"sad", "pout", "embarrassed", "worried"}),
    ("Did you know octopuses have three hearts?", {"surprised", "curious", "excited", "happy"}),
    ("I'm so tired, good night Alice.", {"sleepy", "happy", "neutral", "sad", "worried"}),
    ("Why do cats purr?", {"curious", "happy", "neutral", "excited"}),
    ("What's 15% of 80?", {"neutral", "happy", "curious"}),
    ("You look cute today.", {"embarrassed", "happy"}),
    ("I'm nervous about my exam tomorrow.", {"worried", "curious"}),
]


async def reply(question: str) -> tuple[str, bool]:
    answer = await client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "system", "content": system_prompt()}, {"role": "user", "content": stamped(question)}],
        tools=TOOL_SCHEMAS,
        **REASONING_ARGS,
    )
    message = answer.choices[0].message
    return message.content or "", bool(message.tool_calls)


async def main() -> None:
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    only = sys.argv[2].casefold() if len(sys.argv) > 2 else ""
    cases = [c for c in CASES if only in c[0].casefold()]
    passed = total = 0
    started = time.monotonic()
    for question, fitting in cases:
        lines = []
        good = 0
        for _ in range(runs):
            text, used_tool = await reply(question)
            if used_tool and not text.strip():
                lines.append("            (called a tool first, skipped)")
                continue
            total += 1
            opening = re.match(r"\s*" + TAG.pattern, text, re.IGNORECASE)
            mood = mood_of(opening.group(1)) if opening else None
            shown, _ = (filtered := MoodFilter()).feed(text)
            shown += filtered.flush()
            leaked = re.search(r"\[\s*\w+\s*\]|\(Sent ", shown)
            ok = mood in fitting and not leaked
            good += ok
            if not ok:
                why = "no opening tag" if not opening else f"mood {mood}" if mood not in fitting else f"left {leaked.group(0)}"
                lines.append(f"            {why}: {text.strip()[:110]!r}")
        passed += good
        mark = "PASS" if good == runs else "FLAKY" if good else "FAIL"
        print(f"{mark:5} {good}/{runs}  {question}")
        for line in lines:
            print(line)
    print(f"\nScore: {passed}/{total} ({100 * passed / max(total, 1):.0f}%) in {time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
