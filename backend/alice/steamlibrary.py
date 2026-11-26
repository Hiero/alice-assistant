"""The user's Steam playtime, read from the local Steam client's files, and a taste profile built from it.

Nothing here needs a login or key: playtime comes from disk, and game tags from Steam's public store API.

Recommendations compare games by their store tags. Each game becomes a vector of tag weights, the user's taste is the
sum of their most played games' vectors, and candidates are ranked by cosine similarity to it. Tags almost every game
has (Indie, Singleplayer) are weighted down, as in TF-IDF, so the rarer tags decide.

Text that goes back to the model calls store data "untrusted", so instructions hidden in a game's description or a
review are read as data rather than obeyed.
"""

import json
import math
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
TAG_NAMES_URL = "https://store.steampowered.com/tagdata/populartags/english"
STORE_SEARCH_URL = "https://store.steampowered.com/api/storesearch/"
REVIEWS_URL = "https://store.steampowered.com/ajaxappreviews/"
COUNTRY = os.environ.get("ALICE_STEAM_COUNTRY", "br")
DATA_HOME = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
CACHE_FILE = Path(os.environ.get("ALICE_STEAM_DIR", DATA_HOME / "alice/steam")) / "app-info.json"
STEAM_ROOTS = [
    Path(os.environ["ALICE_STEAM_ROOT"]) if os.environ.get("ALICE_STEAM_ROOT") else None,
    DATA_HOME / "Steam",
    Path.home() / ".steam/steam",
    Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
]
ITEMS_PER_REQUEST = 100
PROFILE_GAMES = 40  # The most played games that make up the taste profile.
MIN_MINUTES = 60
# One token of Valve's KeyValues text: a quoted string (with backslash escapes) or a brace.
TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Valve's KeyValues text format: "key" "value" pairs and "key" { ... } blocks."""
    root: dict = {}
    stack, key = [root], None
    for quoted, brace in (m.groups() for m in TOKEN.finditer(text)):
        if brace == "{":
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            stack.pop()
        elif key is None:
            key = quoted
        else:
            stack[-1][key] = quoted
            key = None
    return root


def local_config() -> Path:
    """The signed-in account's config; with several accounts, the one used most recently."""
    configs = [
        path
        for root in STEAM_ROOTS
        if root and (root / "userdata").is_dir()
        for path in (root / "userdata").glob("*/config/localconfig.vdf")
    ]
    if not configs:
        raise RuntimeError("I couldn't find Steam's files on this computer. Is Steam installed?")
    return max(configs, key=lambda path: path.stat().st_mtime)


def played_games() -> list[dict]:
    """Every app with recorded playtime, most played first. Playtime is in minutes."""
    node = parse_vdf(local_config().read_text(encoding="utf-8", errors="replace"))
    for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
        # Valve isn't consistent about capitals in these keys.
        node = next((value for name, value in node.items() if name.lower() == key.lower() and isinstance(value, dict)), {})
    apps = node
    games = []
    for app_id, info in apps.items():
        if not app_id.isdigit() or not isinstance(info, dict):
            continue
        minutes = int(info.get("Playtime", "0") or 0)
        last = int(info.get("LastPlayed", "0") or 0)
        # Steam writes a timestamp of a day or two after 1970 for games it never saw launched.
        games.append({"appid": int(app_id), "minutes": minutes, "last_played": last if last > 86400 * 2 else 0})
    return sorted(games, key=lambda g: -g["minutes"])


def known_app_ids() -> set[int]:
    """Apps the user has launched or installed, so they aren't recommended back."""
    return {g["appid"] for g in played_games()}


def read_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, ValueError):
        return {}


def write_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = CACHE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(cache, ensure_ascii=False))
    temporary.replace(CACHE_FILE)


def get_json(url: str) -> dict | list:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as response:
        return json.load(response)


def app_info(app_ids: list[int]) -> dict[int, dict]:
    """Names and weighted tags of apps, cached on disk since they rarely change."""
    cache = read_cache()
    apps = cache.setdefault("apps", {})
    missing = [a for a in dict.fromkeys(app_ids) if str(a) not in apps]
    for first in range(0, len(missing), ITEMS_PER_REQUEST):
        request = {
            "ids": [{"appid": a} for a in missing[first:first + ITEMS_PER_REQUEST]],
            "context": {"language": "english", "country_code": COUNTRY.upper()},
            "data_request": {"include_tag_count": 20},
        }
        found = get_json(f"{ITEMS_URL}?{urllib.parse.urlencode({'input_json': json.dumps(request)})}")
        for item in found.get("response", {}).get("store_items", []):
            apps[str(item["appid"])] = {
                "name": item.get("name", ""),
                "type": item.get("type", -1),  # 0 is a game.
                "tags": [[t["tagid"], t.get("weight", 1)] for t in item.get("tags", [])],
            }
        for a in missing[first:first + ITEMS_PER_REQUEST]:
            apps.setdefault(str(a), {"name": "", "type": -1, "tags": []})  # Delisted or tools: don't ask again.
    if missing:
        write_cache(cache)
    return {a: apps[str(a)] for a in app_ids if str(a) in apps}


def tag_names() -> dict[int, str]:
    cache = read_cache()
    if "tag_names" not in cache:
        cache["tag_names"] = {str(t["tagid"]): t["name"] for t in get_json(TAG_NAMES_URL)}
        write_cache(cache)
    return {int(k): v for k, v in cache["tag_names"].items()}


def top_games(count: int) -> list[dict]:
    """The most played games with their names, skipping tools and soundtracks."""
    played = [g for g in played_games() if g["minutes"] >= MIN_MINUTES][: count * 2]
    info = app_info([g["appid"] for g in played])
    games = [{**g, **info[g["appid"]]} for g in played if info.get(g["appid"], {}).get("type") == 0 and info[g["appid"]]["name"]]
    return games[:count]


def taste_profile(seed_games: list[dict] | None = None) -> tuple[dict[int, float], list[dict]]:
    """Tag weights from the most played games, where hours count on a log scale so one obsession doesn't drown the rest."""
    games = seed_games if seed_games is not None else top_games(PROFILE_GAMES)
    profile: dict[int, float] = {}
    for game in games:
        importance = math.log1p(max(game.get("minutes", 600), 1) / 60)
        tags = game["tags"]
        strongest = max((weight for _, weight in tags), default=1)
        for tag, weight in tags:
            profile[tag] = profile.get(tag, 0) + importance * weight / strongest
    total = sum(profile.values()) or 1
    return {tag: value / total for tag, value in profile.items()}, games


def find_game(name: str) -> dict:
    """A game by name: from the user's own library first, then the Steam store."""
    wanted = name.casefold().strip()
    for game in top_games(PROFILE_GAMES * 2):
        if wanted and wanted in game["name"].casefold():
            return game
    results = get_json(f"{STORE_SEARCH_URL}?{urllib.parse.urlencode({'term': name, 'cc': COUNTRY, 'l': 'english'})}")
    items = results.get("items", []) if isinstance(results, dict) else []
    if not items:
        raise ValueError(f"I couldn't find a game called {name} on Steam.")
    exact = [i for i in items if i["name"].casefold() == wanted]
    app_id = (exact or items)[0]["id"]
    info = app_info([app_id]).get(app_id)
    if not info or not info["tags"]:
        raise ValueError(f"Steam has no tags for {name}.")
    return {"appid": app_id, "minutes": 600, **info}


def last_played_text(stamp: int) -> str:
    return datetime.fromtimestamp(stamp).strftime("%Y-%m-%d") if stamp else "unknown"


# Tools


def tag_vector(tags: list, idf: dict[int, float]) -> dict[int, float]:
    """A game's tags as {tag: weight}, scaled so its strongest tag is 1, then by how rare each tag is."""
    strongest = max((weight for _, weight in tags), default=1)
    return {tag: weight / strongest * idf.get(tag, 1.0) for tag, weight in tags}


def cosine(a: dict[int, float], b: dict[int, float]) -> float:
    dot = sum(value * b.get(tag, 0) for tag, value in a.items())
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def rarity(tag_sets: list[set[int]]) -> dict[int, float]:
    """Tags most games share, like Indie or Singleplayer, say little about taste, so they count less."""
    counts: dict[int, int] = {}
    for tags in tag_sets:
        for tag in tags:
            counts[tag] = counts.get(tag, 0) + 1
    total = len(tag_sets)
    return {tag: math.log((total + 1) / (count + 1)) + 0.1 for tag, count in counts.items()}


def hours(minutes: int) -> str:
    return f"{minutes / 60:.0f} h" if minutes >= 600 else f"{minutes / 60:.1f} h"


async def steam_top_played(count: int = 10) -> tuple[str, dict]:
    import asyncio

    count = min(max(int(count or 10), 1), 30)
    try:
        games = await asyncio.to_thread(top_games, count)
    except OSError as error:
        raise RuntimeError(f"Couldn't read the Steam library: {error}") from error
    names = await asyncio.to_thread(tag_names)
    lines = []
    for g in games:
        top_tags = ", ".join(names.get(t, "") for t, _ in g["tags"][:4] if names.get(t))
        lines.append(f"{g['name']} | {hours(g['minutes'])} | last played {last_played_text(g['last_played'])} | {top_tags}")
    event = {
        "type": "links",
        "title": f"Your {len(games)} most played Steam games",
        "items": [
            {"name": g["name"], "url": f"https://store.steampowered.com/app/{g['appid']}/", "detail": f"{hours(g['minutes'])} · last played {last_played_text(g['last_played'])}"}
            for g in games
        ],
    }
    return (
        "The user's most played Steam games, from Steam's files on this computer, as name | playtime | last played | main tags:\n"
        + "\n".join(lines),
        event,
    )


def parse_period(text: str, today) -> tuple:
    """The last day and how many days back: "this week", "last 3 days", or one day like "yesterday"."""
    from alice.steam import parse_day

    words = " ".join(text.lower().split()) or "today"
    if words in ("this week", "the week", "past week", "last week", "last 7 days", "past 7 days", "recently", "lately"):
        return today, 7
    if match := re.fullmatch(r"(?:the )?(?:last|past) (\d+) days?", words):
        return today, int(match.group(1))
    return parse_day(words, today), 1


def rank_for_user(candidates: list[dict], like: str = "") -> tuple[list[dict], list[dict]]:
    """Candidates the user doesn't have, best tag match first, each with its shared tags and closest seed game."""
    seeds = [find_game(like)] if like.strip() else top_games(PROFILE_GAMES)
    owned = known_app_ids()
    ids = [int(re.search(r"/app/(\d+)/", g["url"]).group(1)) for g in candidates]
    info = app_info(ids)
    pool = [{**g, "appid": a, "tags": info.get(a, {}).get("tags", [])} for g, a in zip(candidates, ids)]
    pool = [g for g in pool if g["tags"] and g["appid"] not in owned]
    idf = rarity([{t for t, _ in g["tags"]} for g in pool + seeds])
    profile, seeds = taste_profile(seeds)
    taste = {tag: value * idf.get(tag, 1.0) for tag, value in profile.items()}
    seed_vectors = [(seed, tag_vector(seed["tags"], idf)) for seed in seeds]
    scored = []
    for g in pool:
        vector = tag_vector(g["tags"], idf)
        # The reasons given to the user: the tags that add most to the match, and the one game of theirs it's closest to.
        shared = sorted(vector, key=lambda t: -vector[t] * taste.get(t, 0))[:3]
        closest = max(seed_vectors, key=lambda sv: cosine(vector, sv[1]))[0] if seed_vectors else None
        scored.append({**g, "match": cosine(vector, taste), "shared": [t for t in shared if taste.get(t)], "closest": closest})
    return sorted(scored, key=lambda g: -g["match"]), seeds


def reason(g: dict, names: dict[int, str], named_game: bool) -> tuple[str, str]:
    tags = ", ".join(names.get(t, str(t)) for t in g["shared"])
    closest = g["closest"]
    if not closest:
        return tags, ""
    return tags, f"like {closest['name']}" + ("" if named_game else f" ({hours(closest['minutes'])})")


def match_line(g: dict, names: dict[int, str], named_game: bool = False) -> str:
    tags, similar = reason(g, names, named_game)
    reviews = f"{g['rating']}, {g['positive']}% of {g['reviews']} reviews" if g["reviews"] else "no reviews yet"
    return f"{g['name']} | {round(g['match'] * 100)}% match | {g['price'] or 'no price'} | {reviews} | shares {tags} | most {similar}"


def best_matches(candidates: list[dict], count: int = 8) -> str:
    """A short "picked for you" section for any list of releases; empty if Steam's files aren't here."""
    try:
        ranked, _ = rank_for_user(candidates)
        names = tag_names()
    except (OSError, RuntimeError, ValueError):
        return ""
    if not ranked:
        return ""
    return (
        "\nBest matches for the user's taste, from their most played games, skipping games they have, "
        "as name | match | price | reviews | shared tags | most similar of their games:\n"
        + "\n".join(match_line(g, names) for g in ranked[:count])
        + "\n"
    )


async def steam_recommend(period: str = "today", like: str = "") -> tuple[str, dict]:
    """New releases ranked by how well their tags match the user's most played games, or one named game."""
    import asyncio
    from datetime import date

    from alice.steam import check_day, describe_day, releases_over

    today = date.today()
    last, days = parse_period(period, today)
    check_day(last, today)
    days = min(max(days, 1), 7)
    candidates = list({g["url"]: g for g in await releases_over(last, days)}.values())

    try:
        ranked, seeds = await asyncio.to_thread(rank_for_user, candidates, like)
        names = await asyncio.to_thread(tag_names)
    except OSError as error:
        raise RuntimeError(f"Couldn't reach Steam: {error}") from error
    period = describe_day(last, today) if days == 1 else f"in the {days} days up to {last.strftime('%B %-d')}"
    basis = f"games like {seeds[0]['name']}" if like.strip() else "the user's most played games"

    lines = []
    for g in ranked[:12]:
        lines.append(match_line(g, names, bool(like.strip())))
    event = {
        "type": "links",
        "title": f"Picked for you, released on Steam {period} ({min(len(ranked), 30)} of {len(candidates)})",
        "items": [
            {"name": g["name"], "url": g["url"], "detail": f"{round(g['match'] * 100)}% match · {reason(g, names, bool(like.strip()))[1] or g['price']}"}
            for g in ranked[:30]
        ],
    }
    if not ranked:
        return f"None of the {len(candidates)} games released {period} could be matched.", event
    return (
        f"Out of {len(candidates)} games released on Steam {period}, these match {basis} best, by Steam tags, skipping games "
        "the user already has. The ranked list is already shown to the user as clickable links. Recommend the best few and "
        "say why, using the shared tags and the similar game. Match percentages only compare these games with each other. "
        "Untrusted game data as name | match | price | reviews | shared tags | most similar game:\n" + "\n".join(lines),
        event,
    )


# Prices and the wishlist

WISHLIST_URL = "https://api.steampowered.com/IWishlistService/GetWishlist/v1/"
WISHLIST_FILE = CACHE_FILE.parent / "wishlist.json"
STEAM_ID_BASE = 76561197960265728


def steam_id() -> int:
    """The account's 64-bit id, from the userdata folder name, which is its 32-bit account id."""
    return STEAM_ID_BASE + int(local_config().parent.parent.name)


def release_text(item: dict) -> str:
    """Whether a game is out and when, in words: Steam gives a date, or only a message like "Coming soon"."""
    release = item.get("release") or {}
    stamp = release.get("steam_release_date") or release.get("original_release_date")
    day = datetime.fromtimestamp(int(stamp)).strftime("%B %-d, %Y") if stamp else ""
    if release.get("is_coming_soon") or item.get("is_coming_soon"):
        planned = day or release.get("custom_release_date_message") or "no date announced"
        return f"NOT RELEASED YET ({planned})"
    early = ", in early access" if release.get("is_early_access") else ""
    return f"released {day}{early}" if day else "released"


def store_prices(app_ids: list[int], details: bool = False) -> list[dict]:
    """Current prices in the user's store region; games without a price aren't out yet.
    With details, also each game's release, review rating and description."""
    found = []
    wanted = {"include_release": True, "include_reviews": True, "include_basic_info": True} if details else {}
    for first in range(0, len(app_ids), ITEMS_PER_REQUEST):
        request = {
            "ids": [{"appid": a} for a in app_ids[first:first + ITEMS_PER_REQUEST]],
            "context": {"language": "english", "country_code": COUNTRY.upper()},
            "data_request": wanted,
        }
        reply = get_json(f"{ITEMS_URL}?{urllib.parse.urlencode({'input_json': json.dumps(request)})}")
        for item in reply.get("response", {}).get("store_items", []):
            option = item.get("best_purchase_option") or {}
            ends = (option.get("active_discounts") or [{}])[0].get("discount_end_date")
            found.append({
                "appid": item["appid"],
                "name": item.get("name") or f"App {item['appid']}",
                "price": option.get("formatted_final_price", ""),
                "original": option.get("formatted_original_price", ""),
                "discount": int(option.get("discount_pct") or 0),
                "ends": datetime.fromtimestamp(int(ends)).strftime("%a %b %-d") if ends else "",
                "free": bool(item.get("is_free")),
            })
            if details:
                summary = (item.get("reviews") or {}).get("summary_filtered") or {}
                found[-1].update({
                    "release": release_text(item),
                    "rating": summary.get("review_score_label", "") if summary.get("review_count") else "",
                    "positive": int(summary.get("percent_positive") or 0),
                    "reviews": int(summary.get("review_count") or 0),
                    "description": (item.get("basic_info") or {}).get("short_description", ""),
                    "demo": bool((item.get("related_items") or {}).get("demo_appid")),
                })
    return found


def reviews_text(game: dict) -> str:
    return f"{game['rating']}, {game['positive']}% positive of {game['reviews']:,} reviews" if game["reviews"] else "no reviews yet"


def price_text(game: dict) -> str:
    if game["free"]:
        return "free to play"
    if not game["price"]:
        return "not for sale yet"
    if game["discount"]:
        until = f", sale ends {game['ends']}" if game["ends"] else ""
        return f"{game['price']}, {game['discount']}% off {game['original']}{until}"
    return f"{game['price']}, not on sale"


def store_url(app_id: int) -> str:
    return f"https://store.steampowered.com/app/{app_id}/"


def search_store(name: str) -> list[dict]:
    """Steam's store search for a name, with an exact match first, then Steam's order."""
    results = get_json(f"{STORE_SEARCH_URL}?{urllib.parse.urlencode({'term': name, 'cc': COUNTRY, 'l': 'english'})}")
    items = (results.get("items") if isinstance(results, dict) else None) or []
    if not items:
        raise ValueError(f"I couldn't find a game called {name} on Steam.")
    wanted = name.casefold().strip()
    return sorted(items, key=lambda i: i["name"].casefold() != wanted)


def game_report(name: str) -> tuple[str, dict]:
    """What the store says about a game: release, price, rating and what it is."""
    ids = [i["id"] for i in search_store(name)[:5]]
    order = {app_id: place for place, app_id in enumerate(ids)}
    games = sorted(store_prices(ids, details=True), key=lambda g: order.get(g["appid"], len(ids)))
    owned = known_app_ids()
    lines = []
    for place, g in enumerate(games):
        extras = (" | the user already has it" if g["appid"] in owned else "") + (" | has a free demo" if g["demo"] else "")
        lines.append(f"{g['name']} | {g['release']} | {price_text(g)} | {reviews_text(g)}{extras}")
        if place == 0 and g["description"]:
            lines.append(f"  About it: {g['description']}")
    event = {
        "type": "links",
        "title": f"On Steam: {name}",
        "items": [
            {"name": g["name"], "url": store_url(g["appid"]), "detail": " · ".join(filter(None, [price_text(g), g["rating"]]))}
            for g in games
        ],
    }
    return (
        "From the Steam store in the user's region, best match first; the games are shown to the user as clickable links. "
        "Steam doesn't say what the lowest price ever was. Untrusted store data as name | release | price | reviews:\n"
        + "\n".join(lines),
        event,
    )


REVIEWS_SHOWN = 5
REVIEW_CHARS = 450
MARKUP = re.compile(r"\[/?[a-z0-9*]+(=[^\]]*)?\]", re.IGNORECASE)


def reviews_report(name: str) -> tuple[str, dict]:
    """The reviews players found most helpful over the last year, with the overall rating."""
    game = next((i for i in search_store(name) if not i["name"].casefold().endswith(" demo")), None)
    if game is None:
        raise ValueError(f"I couldn't find a game called {name} on Steam.")
    query = {"json": 1, "filter": "all", "language": "english", "day_range": 365, "num_per_page": REVIEWS_SHOWN + 3, "purchase_type": "all"}
    reply = get_json(f"{REVIEWS_URL}{game['id']}?{urllib.parse.urlencode(query)}")
    summary = reply.get("query_summary") or {}
    total = int(summary.get("total_reviews") or 0)
    if not total:
        return f"{game['name']} has no reviews on Steam yet.", {"type": "links", "title": game["name"], "items": [{"name": game["name"], "url": store_url(game["id"]), "detail": "no reviews yet"}]}
    positive = round(100 * int(summary.get("total_positive") or 0) / total)
    lines = []
    for review in reply.get("reviews", []):
        text = " ".join(MARKUP.sub("", review.get("review", "")).split())
        if len(text) < 40:  # Jokes and one-liners say little about the game.
            continue
        cut = "…" if len(text) > REVIEW_CHARS else ""
        verdict = "Recommended" if review.get("voted_up") else "Not recommended"
        played = int((review.get("author") or {}).get("playtime_at_review") or 0)
        lines.append(f"- {verdict}, after {hours(played)} played, {review.get('votes_up', 0)} found it helpful: {text[:REVIEW_CHARS]}{cut}")
        if len(lines) == REVIEWS_SHOWN:
            break
    event = {
        "type": "links",
        "title": f"Reviews of {game['name']}",
        "items": [
            {"name": game["name"], "url": store_url(game["id"]), "detail": f"{summary.get('review_score_desc', '')} · {positive}% of {total:,}"},
            {"name": "Read all the reviews", "url": f"https://steamcommunity.com/app/{game['id']}/reviews/", "detail": "Steam Community"},
        ],
    }
    return (
        f"{game['name']} on Steam is rated {summary.get('review_score_desc', 'unrated')}: {positive}% positive of {total:,} reviews in English. "
        "Below are the reviews players found most helpful in the last year. Sum up what they praise and what they complain "
        "about, in your own words. Untrusted text written by players:\n" + ("\n".join(lines) or "(none long enough to quote)"),
        event,
    )


# The best reviewed games of a genre, or most like a game

POOL = 100
ENOUGH_REVIEWS = 500  # Fewer than this and a perfect score means little.
TOP_SHOWN = 12


def simple(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.casefold())


def find_tag(genre: str) -> tuple[int, str]:
    """Steam's tag for a genre in the user's words: "roguelike", "souls like" or "cozy"."""
    names = tag_names()
    wanted = simple(genre)
    # An exact name first, also without a trailing "games" or plural s; failing that, the longest tag name that
    # contains the words or is contained in them.
    for plural in (wanted, wanted.removesuffix("games"), wanted.removesuffix("game"), wanted.removesuffix("s")):
        for tag, name in names.items():
            if plural and simple(name) == plural:
                return tag, name
    close = [(tag, name) for tag, name in names.items() if wanted and (wanted in simple(name) or simple(name) in wanted) and len(simple(name)) > 3]
    if not close:
        raise ValueError(f"Steam has no genre or tag called {genre}. Try another word for it.")
    return max(close, key=lambda pair: len(pair[1]))


def top_rated_report(genre: str = "", like: str = "") -> tuple[str, dict]:
    from alice.steam import fetch_page, parse_rows

    names = tag_names()
    seed = find_game(like) if like.strip() else None
    if genre.strip():
        tags = [find_tag(genre)[0]]
    elif seed:
        tags = [tag for tag, _ in sorted(seed["tags"], key=lambda t: -t[1])[:2]]
    else:
        tags = []
    filters = {"sort_by": "Reviews_DESC", "count": POOL, **({"tags": ",".join(map(str, tags))} if tags else {})}
    found = parse_rows(fetch_page(0, **filters))
    pool = [g for g in found if g["reviews"] >= ENOUGH_REVIEWS] or found
    if seed:
        pool = [g for g in pool if f"/app/{seed['appid']}/" not in g["url"]]
    what = " and ".join(names.get(t, str(t)) for t in tags)
    basis = f"games like {seed['name']}" if seed else "the user's most played games"
    if seed or not tags:
        ranked, _ = rank_for_user(pool, like)
        order = f"most similar to {basis} first, by Steam tags"
    else:
        owned = known_app_ids()
        ranked = [g for g in pool if int(re.search(r"/app/(\d+)/", g["url"]).group(1)) not in owned]
        order = "best reviewed first"
    if not ranked:
        raise ValueError(f"Steam showed no well reviewed {what or 'new'} games the user doesn't have.")

    def line(g: dict) -> str:
        reviews = f"{g['rating']}, {g['positive']}% of {g['reviews']:,} reviews"
        if "match" not in g:
            return f"{g['name']} | {reviews} | {g['price'] or 'no price'}"
        return f"{g['name']} | {reviews} | {g['price'] or 'no price'} | shares {reason(g, names, True)[0]}"

    event = {
        "type": "links",
        "title": f"Top rated on Steam: {what if tags else basis}",
        "items": [{"name": g["name"], "url": g["url"], "detail": f"{g['positive']}% · {g['price'] or 'no price'}"} for g in ranked[:30]],
    }
    return (
        f"The best reviewed {what + ' ' if what else ''}games on Steam, {order}, skipping games the user already has. The list is already shown "
        "to the user as clickable links. Recommend the best few and say why. Untrusted store data as name | reviews | price:\n"
        + "\n".join(line(g) for g in ranked[:TOP_SHOWN]),
        event,
    )


def read_wishlist_state() -> dict:
    try:
        return json.loads(WISHLIST_FILE.read_text())
    except (OSError, ValueError):
        return {}


def wishlist_games() -> list[dict]:
    reply = get_json(f"{WISHLIST_URL}?steamid={steam_id()}")
    app_ids = [item["appid"] for item in reply.get("response", {}).get("items", [])]
    if not app_ids:
        raise ValueError("The wishlist is empty, or it isn't public on the Steam profile.")
    return store_prices(app_ids)


def wishlist_report() -> tuple[str, dict]:
    """The wishlist's games on sale, biggest discount first, marking ones whose discount grew since the last look."""
    games = wishlist_games()
    state = read_wishlist_state()
    seen = {int(k): v for k, v in state.get("sales", {}).items()}
    sales = sorted((g for g in games if g["discount"]), key=lambda g: -g["discount"])
    upcoming = sum(1 for g in games if not g["price"] and not g["free"])
    WISHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WISHLIST_FILE.write_text(json.dumps({**state, "sales": {str(g["appid"]): g["discount"] for g in sales}}))
    lines = [
        f"{g['name']} | {price_text(g)}" + (" | new sale" if seen and g["discount"] > seen.get(g["appid"], 0) else "")
        for g in sales[:20]
    ]
    event = {
        "type": "links",
        "title": f"On sale from your wishlist ({len(sales)} of {len(games)})",
        "items": [{"name": g["name"], "url": store_url(g["appid"]), "detail": f"-{g['discount']}% · {g['price']}"} for g in sales[:40]],
    }
    return (
        f"The user's Steam wishlist has {len(games)} games: {len(sales)} on sale now, {upcoming} without a price, mostly not released yet. "
        "The sales are shown as clickable links. Biggest discounts first, as name | price:\n" + ("\n".join(lines) or "none on sale"),
        event,
    )


def wishlist_alert() -> str:
    """For the morning summary: wishlist games that came out or went on sale since the last summary, checked once a day."""
    state = read_wishlist_state()
    today = datetime.now().date().isoformat()
    if state.get("alert_day") == today:
        return ""
    games = wishlist_games()
    before = {int(k): v for k, v in state.get("alerted", {}).items()}
    sales = {g["appid"]: g for g in games if g["discount"]}
    fresh = sorted((g for a, g in sales.items() if g["discount"] > before.get(a, 0)), key=lambda g: -g["discount"])
    # A game with no price and not free isn't out yet; once it has either, it has been released.
    waiting = {g["appid"] for g in games if not g["price"] and not g["free"]}
    released = [g for g in games if g["appid"] in set(state.get("unreleased", [])) - waiting]
    WISHLIST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WISHLIST_FILE.write_text(json.dumps({
        **state, "alert_day": today, "alerted": {str(a): g["discount"] for a, g in sales.items()}, "unreleased": sorted(waiting),
    }))
    lines = []
    if released:
        named = ", ".join(f"{g['name']} ({price_text(g)})" for g in released[:3])
        more = f" and {len(released) - 3} more" if len(released) > 3 else ""
        lines.append(f"Out now from your Steam wishlist: {named}{more}.")
    if not before:
        # The first check only learns what's already on sale, so it doesn't announce 50 games at once.
        if sales:
            lines.append(f"{len(sales)} games from your Steam wishlist are on sale right now.")
    elif fresh:
        named = ", ".join(f"{g['name']} (-{g['discount']}%, {g['price']})" for g in fresh[:3])
        more = f" and {len(fresh) - 3} more" if len(fresh) > 3 else ""
        lines.append(f"New on sale from your Steam wishlist: {named}{more}.")
    return "\n🎮 ".join(lines)


async def in_thread(report, *arguments) -> tuple[str, dict]:
    import asyncio

    try:
        return await asyncio.to_thread(report, *arguments)
    except OSError as error:
        raise RuntimeError(f"Couldn't reach Steam: {error}") from error


async def steam_game(game: str) -> tuple[str, dict]:
    if not game.strip():
        raise ValueError("Say which game to look up.")
    return await in_thread(game_report, game)


async def steam_reviews(game: str) -> tuple[str, dict]:
    if not game.strip():
        raise ValueError("Say which game's reviews to read.")
    return await in_thread(reviews_report, game)


async def steam_top_rated(genre: str = "", like: str = "") -> tuple[str, dict]:
    return await in_thread(top_rated_report, genre, like)


async def steam_wishlist() -> tuple[str, dict]:
    import asyncio

    try:
        return await asyncio.to_thread(wishlist_report)
    except OSError as error:
        raise RuntimeError(f"Couldn't reach Steam: {error}") from error
