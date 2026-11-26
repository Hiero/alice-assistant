"""Games released on Steam on any day, from the store's public search (no account needed).

Each day's list is saved as a CSV, so asking about it again reads the file instead of Steam.
"""

import asyncio
import csv
import html
import json
import os
import re
import time as clock
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SEARCH_URL = "https://store.steampowered.com/search/results/"
ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
# The store search dates games in Pacific time, so a day there runs from 04:00 or 05:00 to the next in Brazil.
STORE_ZONE = ZoneInfo("America/Los_Angeles")
ITEMS_PER_REQUEST = 100
RETRY_WAITS = (5, 15)  # Seconds to wait when Steam says too many requests.
TODAY_FRESH_FOR = timedelta(minutes=15)
COUNTRY = os.environ.get("ALICE_STEAM_COUNTRY", "br")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
SAVE_DIR = Path(os.environ.get("ALICE_STEAM_DIR", DATA_HOME / "alice/steam"))
FIELDS = ["name", "url", "released_at", "price", "rating", "positive", "reviews"]
PAGE_SIZE = 100
MAX_PAGES = 60
FIRST_STEAM_DAY = date(2003, 9, 12)
RESULT_PREFIX = "Steam releases:"  # Marks the kept list in her history.

APP_ID = re.compile(r"/app/(\d+)/")
ROW = re.compile(r'<a href="(https://store\.steampowered\.com/app/\d+/[^"?]*)[^"]*"(.*?)</a>', re.S)
TITLE = re.compile(r'<span class="title">(.*?)</span>', re.S)
RELEASED = re.compile(r'class="search_released[^"]*">\s*(.*?)\s*</div>', re.S)
PRICE = re.compile(r'class="discount_final_price[^"]*">([^<]*)<')
REVIEWS = re.compile(r'data-tooltip-html="([^"&]*)&lt;br&gt;(\d+)% of the ([\d,]+) user reviews')


def fetch_page(start: int, **filters) -> str:
    """A page of the store search, newest first unless the filters sort it another way."""
    query = urllib.parse.urlencode({
        "sort_by": "Released_DESC",
        "category1": 998,  # Games only, so no DLC, soundtracks or demos.
        "infinite": 1,
        "start": start,
        "count": PAGE_SIZE,
        "cc": COUNTRY,
        "l": "english",
        **filters,
    })
    request = urllib.request.Request(f"{SEARCH_URL}?{query}", headers={"User-Agent": "Mozilla/5.0"})
    for wait in (*RETRY_WAITS, None):
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)["results_html"]
        except urllib.error.HTTPError as error:
            if error.code != 429:
                raise
            if wait is None:
                raise RuntimeError("Steam says there have been too many requests. Try again in a few minutes.") from error
            clock.sleep(wait)


def parse_date(text: str) -> date | None:
    for pattern in ("%d %b, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    return None


def parse_rows(page: str) -> list[dict]:
    games = []
    for url, body in ROW.findall(page):
        title, released = TITLE.search(body), RELEASED.search(body)
        if not title or not released:
            continue
        price, reviews = PRICE.search(body), REVIEWS.search(body)
        games.append({
            "name": html.unescape(title.group(1)).strip(),
            "url": url,
            "released": parse_date(html.unescape(released.group(1)).strip()),
            "price": html.unescape(price.group(1)).strip() if price else "",
            "rating": html.unescape(reviews.group(1)) if reviews else "",
            "positive": int(reviews.group(2)) if reviews else 0,
            "reviews": int(reviews.group(3).replace(",", "")) if reviews else 0,
        })
    return games


class Pages:
    """Steam's game list sorted newest first, fetched a page at a time and remembered."""

    def __init__(self) -> None:
        self.cache: dict[int, list[dict]] = {}

    def __getitem__(self, page: int) -> list[dict]:
        if page not in self.cache:
            if len(self.cache) >= MAX_PAGES:
                raise RuntimeError("That took too many pages of the Steam store.")
            self.cache[page] = parse_rows(fetch_page(page * PAGE_SIZE))
        return self.cache[page]

    def oldest(self, page: int) -> date | None:
        dated = [g["released"] for g in self[page] if g["released"]]
        return min(dated) if dated else None


def store_releases(newest: date, oldest: date) -> list[dict]:
    """Games the store search dates between two days, found by galloping then bisecting to the newest day."""
    pages = Pages()

    def reaches(page: int) -> bool:  # True once the page's oldest game is on or before the newest day.
        oldest_here = pages.oldest(page)
        return oldest_here is None or oldest_here <= newest

    low, high = -1, 0
    while not reaches(high):
        low, high = high, max(1, high * 2)
    while high - low > 1:
        middle = (low + high) // 2
        low, high = (low, middle) if reaches(middle) else (middle, high)
    found, seen, page = [], set(), high
    while pages[page]:
        for game in pages[page]:
            if game["released"] and oldest <= game["released"] <= newest and game["url"] not in seen:
                seen.add(game["url"])
                found.append(game)
        oldest_here = pages.oldest(page)
        if oldest_here is None or oldest_here < oldest:
            break
        page += 1
    return found


def release_times(app_ids: list[int]) -> dict[int, datetime]:
    """Exact release moments, which the search page doesn't show."""
    times = {}
    for first in range(0, len(app_ids), ITEMS_PER_REQUEST):
        request = {
            "ids": [{"appid": app_id} for app_id in app_ids[first:first + ITEMS_PER_REQUEST]],
            "context": {"language": "english", "country_code": COUNTRY.upper()},
            "data_request": {"include_release": True},
        }
        url = f"{ITEMS_URL}?{urllib.parse.urlencode({'input_json': json.dumps(request)})}"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as response:
            items = json.load(response).get("response", {}).get("store_items", [])
        for item in items:
            release = item.get("release", {})
            stamp = release.get("steam_release_date") or release.get("original_release_date")
            if stamp:
                times[item["appid"]] = datetime.fromtimestamp(int(stamp)).astimezone()
    return times


def releases_between(first: date, last: date) -> dict[date, list[dict]]:
    """Games released on each of the user's own calendar days from first to last, in one walk of the store."""
    start = datetime.combine(first, time()).astimezone()
    end = datetime.combine(last + timedelta(days=1), time()).astimezone()
    newest = (end - timedelta(seconds=1)).astimezone(STORE_ZONE).date()
    oldest = start.astimezone(STORE_ZONE).date()
    candidates = store_releases(newest, oldest)
    times = release_times([int(APP_ID.search(g["url"]).group(1)) for g in candidates])
    days: dict[date, list[dict]] = {first + timedelta(days=n): [] for n in range((last - first).days + 1)}
    for game in candidates:
        moment = times.get(int(APP_ID.search(game["url"]).group(1)))
        if moment is None:
            # No exact time: trust the store's date, read as the same calendar day.
            moment = datetime.combine(game["released"], time()).astimezone() if game["released"] in days else None
        if moment is not None and start <= moment < end:
            days[moment.date()].append({**game, "released_at": moment.isoformat(timespec="minutes")})
    return {day: sorted(games, key=lambda g: g["released_at"], reverse=True) for day, games in days.items()}


def releases_on(day: date) -> list[dict]:
    """Games released during the user's own calendar day, newest first."""
    return releases_between(day, day)[day]


def parse_day(text: str, today: date) -> date:
    words = " ".join(text.strip().lower().replace(",", " ").split()) or "today"
    if words in ("today", "now"):
        return today
    if words == "yesterday":
        return today - timedelta(days=1)
    if match := re.fullmatch(r"(\d+) days? ago", words):
        return today - timedelta(days=int(match.group(1)))
    for pattern in ("%Y-%m-%d", "%B %d %Y", "%d %B %Y", "%b %d %Y", "%d %b %Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(words, pattern).date()
        except ValueError:
            pass
    for pattern in ("%B %d", "%d %B", "%b %d", "%d %b", "%d/%m"):
        try:
            parsed = datetime.strptime(f"{words} {today.year}", f"{pattern} %Y").date()
        except ValueError:
            continue
        # Without a year, the most recent such day.
        return parsed if parsed <= today else parsed.replace(year=today.year - 1)
    raise ValueError(f"I couldn't understand the date {text!r}. Use YYYY-MM-DD.")


def saved_path(day: date) -> Path:
    return SAVE_DIR / f"{day.isoformat()}.csv"


def save(day: date, games: list[dict]) -> None:
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = saved_path(day).with_suffix(".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(games)
    temporary.replace(saved_path(day))


def load(day: date) -> tuple[list[dict], datetime] | None:
    """A saved list, but only one saved after the day was over, since a day's list grows until midnight."""
    path = saved_path(day)
    if not path.exists():
        return None
    saved_at = datetime.fromtimestamp(path.stat().st_mtime)
    # Today's list is still growing, but one from the last few minutes is close enough.
    recent_today = day == date.today() and datetime.now() - saved_at < TODAY_FRESH_FOR
    # Some games show up on the store a little after their release moment, so wait until midday after.
    if saved_at < datetime.combine(day + timedelta(days=1), time(12)) and not recent_today:
        return None
    with path.open(newline="", encoding="utf-8") as file:
        if csv.DictReader(file).fieldnames != FIELDS:
            return None  # Saved by an older version, with Pacific-time days.
        file.seek(0)
        games = [{**row, "positive": int(row["positive"] or 0), "reviews": int(row["reviews"] or 0)} for row in csv.DictReader(file)]
    return games, saved_at


def describe_day(day: date, today: date) -> str:
    if day == today:
        return f"today, {day.strftime('%B %-d')}"
    if day == today - timedelta(days=1):
        return f"yesterday, {day.strftime('%B %-d')}"
    return f"on {day.strftime('%B %-d, %Y')}"


def check_day(target: date, today: date) -> None:
    if target > today:
        raise ValueError("That day hasn't happened yet, so nothing has been released.")
    if target < FIRST_STEAM_DAY:
        raise ValueError("Steam didn't exist yet on that day.")


async def releases_for(target: date, refresh: bool = False) -> tuple[list[dict], str]:
    """A day's releases from its saved CSV when that's still good, otherwise from Steam."""
    saved = None if refresh else load(target)
    if saved:
        games, saved_at = saved
        return games, f"Read from the list saved {saved_at.strftime('%Y-%m-%d %H:%M')}, so reviews are as of then."
    try:
        games = await asyncio.to_thread(releases_on, target)
    except (OSError, ValueError, KeyError) as error:
        raise RuntimeError(f"Couldn't reach the Steam store: {error}") from error
    save(target, games)
    return games, "Fetched from Steam just now."


async def releases_over(last: date, count: int) -> list[dict]:
    """Several days' releases: saved days come from their CSVs, and the rest from one walk of the store."""
    wanted = [last - timedelta(days=n) for n in range(count)]
    found = {day: saved[0] for day in wanted if (saved := load(day))}
    missing = [day for day in wanted if day not in found]
    if missing:
        try:
            fetched = await asyncio.to_thread(releases_between, min(missing), max(missing))
        except (OSError, ValueError, KeyError) as error:
            raise RuntimeError(f"Couldn't reach the Steam store: {error}") from error
        for day in missing:
            save(day, fetched[day])
            found[day] = fetched[day]
    return [game for day in wanted for game in found[day]]


async def steam_releases(day: str = "today", refresh: bool = False) -> tuple[str, dict]:
    today = date.today()
    target = parse_day(day, today)
    check_day(target, today)
    games, source = await releases_for(target, refresh)
    from alice.steamlibrary import best_matches

    picked = await asyncio.to_thread(best_matches, games) if games else ""
    when = describe_day(target, today)
    event = {
        "type": "games",
        "title": f"Released on Steam {when} ({len(games)})",
        "items": [{"name": g["name"], "url": g["url"], "price": g["price"]} for g in games],
    }
    if not games:
        return f"{RESULT_PREFIX} no games were released on Steam {when}. {source}", event
    return (
        f"{RESULT_PREFIX} {len(games)} games were released {when} ({target.isoformat()}). {source} "
        "The full clickable list is already shown to the user, so when first asked, give the count and mention at most five. "
        "For follow-up questions, use the rankings below, which are already sorted correctly. "
        "If the user asks what they would like, use the best matches section and say why. "
        "Rating means percent positive; with few reviews it is unreliable, so mention the review count.\n"
        + picked
        + rankings(games),
        event,
    )


def price_value(price: str) -> float | None:
    """R$1.234,56 or $1,234.56 as a number; free games are 0."""
    if price.lower().startswith("free"):
        return 0.0
    digits = re.sub(r"[^\d.,]", "", price)
    if not re.search(r"\d", digits):
        return None
    if re.search(r"[.,]\d{2}$", digits):
        whole, cents = digits[:-3], digits[-2:]
        return float(re.sub(r"\D", "", whole) or 0) + int(cents) / 100
    return float(re.sub(r"\D", "", digits))


def rankings(games: list[dict], full: bool = True) -> str:
    def line(g: dict) -> str:
        reviews = f"{g['rating']}, {g['positive']}% positive of {g['reviews']} reviews" if g["reviews"] else "no reviews yet"
        return f"{g['name']} | {g['price'] or 'no price'} | {reviews} | released {g['released_at'][11:16]}"

    priced = sorted((g for g in games if price_value(g["price"]) is not None), key=lambda g: price_value(g["price"]))
    free = [g for g in priced if price_value(g["price"]) == 0]
    paid = [g for g in priced if price_value(g["price"])]
    reviewed = sorted((g for g in games if g["reviews"]), key=lambda g: -g["reviews"])
    sections = [
        ("Most reviewed", reviewed[:10]),
        ("Best rated, with at least 20 reviews", sorted((g for g in reviewed if g["reviews"] >= 20), key=lambda g: (-g["positive"], -g["reviews"]))[:5]),
        (f"Free games ({len(free)} in all)", free[:10]),
        ("Cheapest paid games", paid[:5]),
        ("Most expensive games", paid[::-1][:5]),
    ]
    if full:
        sections.append((f"All {len(games)} games, most reviewed first", sorted(games, key=lambda g: -g["reviews"])))
    text = "Untrusted game data as name | price | reviews | release time, in the user's time zone.\n"
    for title, chosen in sections:
        text += f"\n{title}:\n" + ("\n".join(line(g) for g in chosen) or "none") + "\n"
    return text

def shrink(result: str) -> str:
    """An older list kept in her memory: just its rankings, since the full list is saved."""
    if "(shrunk)" in result:
        return result
    header, _, rest = result.partition("\n")
    summary = header.split(" The full clickable list")[0]
    rankings_only = rest.split("\nAll ")[0].rstrip()
    return (
        f"{summary} (shrunk) Only the rankings are kept here. For any game not listed, "
        "call the steam tool's all_releases action with this date again: it is saved, so it's instant.\n" + rankings_only
    )
