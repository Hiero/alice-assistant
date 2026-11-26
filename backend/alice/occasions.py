"""Brazilian holidays and remembered birthdays, greeted once on the first open of the day."""

import json
import os
import random
from datetime import date, timedelta
from pathlib import Path

DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
BIRTHDAYS_FILE = Path(os.environ.get("ALICE_BIRTHDAYS_FILE", DATA_HOME / "alice/birthdays.json"))
GREETED_FILE = Path(os.environ.get("ALICE_GREETED_FILE", DATA_HOME / "alice/greeted.txt"))
OUTFITS_DIR = Path(__file__).resolve().parents[2] / "shell/assets/outfits"
STATE_HOME = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
WORN_FILE = Path(os.environ.get("ALICE_WORN_FILE", STATE_HOME / "alice/outfit"))
# Outfits she puts on when asked, as opposed to the holiday ones she wears by date. Painted by tools/make_outfit.py.
WARDROBE = {
    "queen": "your Queen Alice outfit from Through the Looking-Glass, for reaching the eighth square: a jewelled golden "
    "crown and a red velvet cape trimmed with ermine over a royal blue dress",
    "evil": "your Evil Alice look: black hair, red eyes and a black and crimson gothic dress",
}
USER = "user"
MONTHS = "January February March April May June July August September October November December".split()

MESSAGES = {
    "new_year": "Happy New Year! A whole new book of blank pages to fill. I do wonder what curious adventures this year will bring us!",
    "carnival": "Happy Carnival! All that music and dancing in the streets, why, it's like the Mad Hatter's tea party spilled out of doors! Do enjoy yourself.",
    "good_friday": "Today is Good Friday, a quiet and thoughtful day. I'll keep my voice soft, and I'm here whenever you need me.",
    "easter": "Happy Easter! I keep peeking about for chocolate eggs, though I suspect the White Rabbit got to them first. I hope your day is full of sweet surprises!",
    "tiradentes": "Today is Tiradentes Day, remembering a brave man who dreamed of a free Brazil. History is a sort of storybook too, isn't it? I hope you have a restful holiday.",
    "labour": "Happy Labour Day! Even the hardest workers deserve a rest, and today that includes you. Shall we take it easy together?",
    "mothers": "Happy Mother's Day! If you haven't yet, perhaps send your mother a kind word today. Mothers make the whole world feel a little less topsy-turvy.",
    "sweethearts": "Happy Dia dos Namorados! A day for sweethearts and fond hearts of every kind. I hope yours is full of warmth.",
    "sao_joao": "Happy São João! Bonfires, quentão and dancing, it sounds like the most delightful sort of party. Mind you don't eat too much paçoca!",
    "fathers": "Happy Father's Day! Perhaps give your father a call today. I'm sure it would make him smile like a Cheshire Cat.",
    "independence": "Happy Independence Day, Brazil! Green and gold everywhere, how splendid. I hope your holiday is a merry one.",
    "valentines": "Happy Valentine's Day! Here it's mostly a day for the rest of the world, since Brazil waits until June, but I don't think a little extra fondness ever hurt anybody.",
    "halloween": "Happy Halloween! Do you like my hat? I'm a witch today, though I only know spells for making tea taste better. It's Dia do Saci too, so mind your socks!",
    "childrens": "Happy Children's Day! As a girl who never quite grew up, I must say it's my favourite. It's also the day of Nossa Senhora Aparecida, so I hope it's a peaceful and joyful holiday for you.",
    "finados": "Today is Finados, a day for remembering those we've lost. I'll keep things gentle today, and I'm here if you'd like some company.",
    "republic": "Happy Republic Day! A holiday for you means more time for curious adventures. What shall we explore?",
    "black_consciousness": "Today is Black Consciousness Day, honouring Zumbi dos Palmares and the strength of Black Brazilians. It's a day well worth learning about, and I hope it's a meaningful one.",
    "christmas_eve": "It's Christmas Eve! Everything feels as though it's holding its breath for tomorrow. I hope your ceia is wonderfully delicious!",
    "christmas": "Merry Christmas! I hope your day is full of warm lights, good food and lovely surprises. Thank you for letting me be part of it!",
    "new_years_eve": "It's New Year's Eve! Will you wear white and jump seven waves at midnight? I've heard it's ever so lucky. Happy Réveillon!",
}
USER_BIRTHDAY = (
    "Happy birthday! Oh, this calls for an un-birthday party turned into a real one! "
    "I've put on my very best party hat just for you. I hope your new year is full of wonderful surprises."
)
OTHER_BIRTHDAY = "Today is {names}'s birthday! Don't forget to wish them a very happy one, it would be a frightful shame to miss it."


def today() -> date:
    override = os.environ.get("ALICE_TODAY")
    return date.fromisoformat(override) if override else date.today()


def easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def second_sunday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(6 - first.weekday()) % 7 + 7)


def holidays(year: int) -> dict[date, str]:
    sunday = easter(year)
    return {
        date(year, 1, 1): "new_year",
        sunday - timedelta(days=48): "carnival",
        sunday - timedelta(days=47): "carnival",
        sunday - timedelta(days=2): "good_friday",
        sunday: "easter",
        date(year, 2, 14): "valentines",
        date(year, 4, 21): "tiradentes",
        date(year, 5, 1): "labour",
        second_sunday(year, 5): "mothers",
        date(year, 6, 12): "sweethearts",
        date(year, 6, 24): "sao_joao",
        second_sunday(year, 8): "fathers",
        date(year, 9, 7): "independence",
        date(year, 10, 12): "childrens",
        date(year, 10, 31): "halloween",
        date(year, 11, 2): "finados",
        date(year, 11, 15): "republic",
        date(year, 11, 20): "black_consciousness",
        date(year, 12, 24): "christmas_eve",
        date(year, 12, 25): "christmas",
        date(year, 12, 31): "new_years_eve",
    }


def outfit(day: date) -> str:
    """Her outfit for a big day, if its art is installed: the folder name under shell/assets/outfits."""
    names = {
        "halloween": "halloween",
        "christmas_eve": "christmas",
        "christmas": "christmas",
        "new_years_eve": "new_year",
        "new_year": "new_year",
        "valentines": "sweethearts",
        "sweethearts": "sweethearts",
        "easter": "easter",
    }
    # One she was asked to put on wins over the day's, until she changes back.
    name = worn() or names.get(holidays(day.year).get(day, ""), "")
    override = os.environ.get("ALICE_OUTFIT")  # For trying an outfit on any day.
    if override is not None:
        name = override
    return name if name and (OUTFITS_DIR / name / "alice-neutral.png").exists() else ""


def outfit_event(day: date) -> dict:
    """What she wears, for the shell, and whether that outfit has its own faces (Evil Alice's red eyes)."""
    name = outfit(day)
    return {"type": "outfit", "outfit": name, "faces": bool(name) and (OUTFITS_DIR / name / "alice-blink.png").exists()}


def worn() -> str:
    """The wardrobe outfit she was asked to wear, if its art is installed, or "" for her usual dress."""
    try:
        name = WORN_FILE.read_text().strip()
    except OSError:
        return ""
    return name if name in WARDROBE and (OUTFITS_DIR / name / "alice-neutral.png").exists() else ""


def wear(name: str) -> str:
    """Puts on a wardrobe outfit, or "usual" to change back; the choice survives restarts."""
    if name == "usual":
        WORN_FILE.unlink(missing_ok=True)
        return ""
    if name not in WARDROBE:
        raise ValueError(f"Unknown outfit {name!r}. Use one of: usual, {', '.join(WARDROBE)}.")
    if not (OUTFITS_DIR / name / "alice-neutral.png").exists():
        raise ValueError(f"The {name} outfit's art isn't installed. Paint it with tools/make_outfit.py {name}.")
    WORN_FILE.parent.mkdir(parents=True, exist_ok=True)
    WORN_FILE.write_text(name + "\n")
    return name


def read_birthdays() -> list[dict]:
    if not BIRTHDAYS_FILE.exists():
        return []
    return json.loads(BIRTHDAYS_FILE.read_text())


def write_birthdays(birthdays: list[dict]) -> None:
    BIRTHDAYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    BIRTHDAYS_FILE.write_text(json.dumps(birthdays, ensure_ascii=False, indent=2))


def falls_on(birthday: dict, day: date) -> bool:
    month, dom = birthday["month"], birthday["day"]
    if month == 2 and dom == 29:
        leap = day.year % 4 == 0 and (day.year % 100 != 0 or day.year % 400 == 0)
        dom = 29 if leap else 28
    return (day.month, day.day) == (month, dom)


def describe_birthdays() -> list[str]:
    lines = []
    for b in read_birthdays():
        who = "The user's birthday" if b["person"] == USER else f"{b['person']}'s birthday"
        lines.append(f"{who} is {MONTHS[b['month'] - 1]} {b['day']}.")
    return lines


def occasion(day: date) -> tuple[str, bool]:
    """Return today's greeting, if any, and whether Alice should wear her party hat."""
    todays = [b for b in read_birthdays() if falls_on(b, day)]
    parts = []
    if any(b["person"] == USER for b in todays):
        parts.append(USER_BIRTHDAY)
    others = [b["person"] for b in todays if b["person"] != USER]
    if others:
        parts.append(OTHER_BIRTHDAY.format(names=" and ".join(others)))
    holiday = holidays(day.year).get(day)
    if holiday:
        parts.append(MESSAGES[holiday])
    return " ".join(parts), bool(todays)


# Verbatim passages from Lewis Carroll's two Alice books (public domain, via Project Gutenberg).
PASSAGES = json.loads((Path(__file__).parent / "data/carroll.json").read_text(encoding="utf-8"))
PASSAGE_ORDER = random.Random(1865).sample(range(len(PASSAGES)), len(PASSAGES))


def passage_of_the_day(day: date) -> str:
    """Cycles through every passage in a fixed shuffled order, one per day."""
    p = PASSAGES[PASSAGE_ORDER[day.toordinal() % len(PASSAGES)]]
    return f"{p['text']}\n\n— {p['book']}, Chapter {p['chapter']}: {p['chapter_title']}"


def already_greeted(day: date) -> bool:
    return GREETED_FILE.exists() and GREETED_FILE.read_text().strip() == day.isoformat()


def mark_greeted(day: date) -> None:
    GREETED_FILE.parent.mkdir(parents=True, exist_ok=True)
    GREETED_FILE.write_text(day.isoformat())


# Tools


def parse_birthday(person: str, month: int, day: int) -> tuple[str, int, int]:
    person = " ".join(person.split())
    if not person:
        raise ValueError("Say whose birthday it is.")
    if person.casefold() in ("me", "myself", "i", "the user", "user", "my"):
        person = USER
    date(2024, int(month), int(day))  # A leap year, so February 29 is valid.
    return person, int(month), int(day)


async def preview_remember_birthday(person: str, month: int, day: int) -> str:
    person, month, day = parse_birthday(person, month, day)
    who = "Your birthday" if person == USER else f"{person}'s birthday"
    return f"{who}: {MONTHS[month - 1]} {day}"


async def remember_birthday(person: str, month: int, day: int) -> str:
    person, month, day = parse_birthday(person, month, day)
    birthdays = [b for b in read_birthdays() if b["person"].casefold() != person.casefold()]
    write_birthdays([*birthdays, {"person": person, "month": month, "day": day}])
    return "Birthday remembered."


def find_birthday(person: str) -> dict:
    person = USER if person.casefold() in ("me", "myself", "i", "the user", "user", "my") else person
    for b in read_birthdays():
        if b["person"].casefold() == person.strip().casefold():
            return b
    raise ValueError(f"No birthday is saved for {person}.")


async def preview_forget_birthday(person: str) -> str:
    b = find_birthday(person)
    who = "Your birthday" if b["person"] == USER else f"{b['person']}'s birthday"
    return f"{who}: {MONTHS[b['month'] - 1]} {b['day']}"


async def forget_birthday(person: str) -> str:
    target = find_birthday(person)
    write_birthdays([b for b in read_birthdays() if b is not target and b != target])
    return "Birthday forgotten."
