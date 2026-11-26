"""Her mood, which she sets with a tag like [happy] at the start of a reply.

The tags drive her face in the shell and are taken out of the text before it is shown or
spoken. Her replies keep them in the conversation history, so she sees her own moods.
"""

import re

MOODS = ("neutral", "happy", "excited", "surprised", "curious", "worried", "sad", "pout", "embarrassed", "sleepy")
# Words a model reaches for instead of the listed ones.
ALIASES = {
    "joy": "happy", "joyful": "happy", "glad": "happy", "pleased": "happy", "cheerful": "happy",
    "delighted": "excited", "thrilled": "excited",
    "shocked": "surprised", "amazed": "surprised",
    "thinking": "curious", "thoughtful": "curious", "wondering": "curious",
    "concerned": "worried", "nervous": "worried", "anxious": "worried", "scared": "worried",
    "upset": "sad", "sorry": "sad",
    "annoyed": "pout", "angry": "pout", "grumpy": "pout", "huffy": "pout", "sulky": "pout", "pouting": "pout",
    "shy": "embarrassed", "flustered": "embarrassed", "blushing": "embarrassed",
    "tired": "sleepy", "calm": "neutral",
    # Evil Alice's favourites.
    "smug": "happy", "smirk": "happy", "smirking": "happy", "gleeful": "happy",
    "unimpressed": "pout", "irritated": "pout", "disdainful": "pout", "sarcastic": "pout", "bored": "pout",
}
TAG = re.compile(r"\[\s*(?:mood\s*[:=]\s*)?([A-Za-z]+)\s*\]", re.IGNORECASE)
# An unclosed "[" this close to the end may be a tag still arriving.
LONGEST_TAG = 24


def mood_of(word: str) -> str | None:
    word = word.casefold()
    return word if word in MOODS else ALIASES.get(word)


class MoodFilter:
    """Takes mood tags out of streamed text: feed() returns the clean text and the moods found."""

    def __init__(self) -> None:
        self._held = ""
        self._started = False
        # The last character already let through, so a space isn't doubled across chunks.
        self._last = ""

    def feed(self, text: str) -> tuple[str, list[str]]:
        text = self._held + text
        self._held = ""
        pieces: list[str] = []
        moods: list[str] = []
        position = 0
        for match in TAG.finditer(text):
            mood = mood_of(match.group(1))
            if mood is None:
                continue
            pieces.append(text[position : match.start()])
            moods.append(mood)
            position = match.end()
            # Drop the space after a tag at the start or after another space, so none doubles up.
            before = self._last + "".join(pieces)
            if position < len(text) and text[position] in " \t" and (not before.strip() or before.endswith((" ", "\n"))):
                position += 1
        rest = text[position:]
        bracket = rest.rfind("[")
        if bracket != -1 and "]" not in rest[bracket:] and len(rest) - bracket <= LONGEST_TAG:
            self._held = rest[bracket:]
            rest = rest[:bracket]
        pieces.append(rest)
        clean = "".join(pieces)
        if not self._started:
            clean = clean.lstrip()
            self._started = bool(clean)
        if clean:
            self._last = clean[-1]
        return clean, moods

    def flush(self) -> str:
        """Text held back in case it was a tag, when the reply ends without one."""
        held, self._held = self._held, ""
        return held

