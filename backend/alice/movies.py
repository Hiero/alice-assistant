"""Films and TV series: a spoiler-free premise, ratings, reviews and where to stream them.

The Movie Database (TMDB) finds the title and gives its premise, cast, user reviews and streaming services;
OMDb adds the IMDb, Rotten Tomatoes and Metacritic scores. Both need a free key in ~/.config/alice/env:
TMDB_API_KEY (this product uses the TMDB API but is not endorsed or certified by TMDB) and OMDB_API_KEY.
"""

import asyncio
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

TMDB = "https://api.themoviedb.org/3"
OMDB = "https://www.omdbapi.com/"
REGION = os.environ.get("ALICE_WATCH_REGION", "BR").upper()
REVIEWS_SHOWN = 5
REVIEW_CHARS = 500  # The opening of a review is where it judges; plot tends to come later.
CAST_SHOWN = 5
MARKUP = re.compile(r"<[^>]+>|\*\*|__|\[|\]\([^)]*\)")
# A review that talks about spoilers is left out, unless it promises to have none.
SPOILERS = re.compile(r"spoil", re.IGNORECASE)
NO_SPOILERS = re.compile(r"(spoiler[- ]?free|no spoilers|without spoilers|non[- ]?spoiler|spoilers? ahead: none)", re.IGNORECASE)
KINDS = {"movie": "movie", "film": "movie", "series": "tv", "show": "tv", "tv": "tv", "tv series": "tv"}


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Alice assistant", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def tmdb(path: str, **params) -> dict:
    key = os.environ.get("TMDB_API_KEY", "").strip()
    if not key:
        raise ValueError("Films and series need a TMDB key: set TMDB_API_KEY in ~/.config/alice/env.")
    auth = {} if len(key) > 40 else {"api_key": key}  # A long key is a v4 read token, sent as a header instead.
    request = urllib.request.Request(
        f"{TMDB}{path}?{urllib.parse.urlencode({**auth, **params})}",
        headers={"Accept": "application/json", **({"Authorization": f"Bearer {key}"} if not auth else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code == 401:
            raise ValueError("TMDB refused the key in TMDB_API_KEY.") from error
        raise


def omdb(imdb_id: str) -> dict:
    """IMDb, Rotten Tomatoes and Metacritic scores, or nothing if there's no key or OMDb is down."""
    key = os.environ.get("OMDB_API_KEY", "").strip()
    if not key or not imdb_id:
        return {}
    try:
        reply = get_json(f"{OMDB}?{urllib.parse.urlencode({'apikey': key, 'i': imdb_id})}")
    except (OSError, ValueError):
        return {}
    return reply if reply.get("Response") == "True" else {}


def year_of(item: dict) -> str:
    return (item.get("release_date") or item.get("first_air_date") or "")[:4]


def find(title: str, kind: str = "", year: str = "") -> dict:
    """The film or series the user most likely means: an exact title first, then the best known."""
    media = KINDS.get(kind.strip().lower(), "")
    params = {"query": title, "include_adult": "false", "language": "en-US"}
    if media:
        if year.isdigit():
            params["year" if media == "movie" else "first_air_date_year"] = year
        results = [dict(r, media_type=media) for r in tmdb(f"/search/{media}", **params).get("results", [])]
    else:
        results = [r for r in tmdb("/search/multi", **params).get("results", []) if r.get("media_type") in ("movie", "tv")]
    if year.isdigit():
        results = [r for r in results if year_of(r) == year] or results
    if not results:
        raise ValueError(f"I couldn't find a film or series called {title}.")
    wanted = title.casefold().strip()
    exact = [r for r in results if wanted in ((r.get("title") or r.get("name") or "").casefold(), (r.get("original_title") or r.get("original_name") or "").casefold())]
    return max(exact or results[:3], key=lambda r: (r.get("popularity") or 0) * (1 + (r.get("vote_count") or 0) ** 0.5))


def details(found: dict) -> dict:
    media = found["media_type"]
    extra = "external_ids,watch/providers,credits" + (",release_dates" if media == "movie" else ",content_ratings")
    info = tmdb(f"/{media}/{found['id']}", language="en-US", append_to_response=extra)
    if not info.get("overview"):  # Some titles are only described in Portuguese.
        info["overview"] = tmdb(f"/{media}/{found['id']}", language="pt-BR").get("overview", "")
    info["media_type"] = media
    return info


def name_of(info: dict) -> str:
    return info.get("title") or info.get("name") or "?"


def scores(info: dict, extra: dict) -> list[str]:
    found = []
    if info.get("vote_count"):
        found.append(f"TMDB {info['vote_average']:.1f}/10 ({info['vote_count']:,} votes)")
    if extra.get("imdbRating", "N/A") != "N/A":
        found.append(f"IMDb {extra['imdbRating']}/10 ({extra.get('imdbVotes', '?')} votes)")
    for rating in extra.get("Ratings", []):
        if rating.get("Source") in ("Rotten Tomatoes", "Metacritic"):
            found.append(f"{rating['Source']} {rating['Value']}")
    return found


def streaming(info: dict) -> tuple[str, str]:
    """Where to watch it in the user's country, and the page that lists the services."""
    offers = (info.get("watch/providers") or {}).get("results", {}).get(REGION) or {}
    kinds = {"flatrate": "streaming on", "free": "free on", "ads": "free with ads on", "rent": "to rent on", "buy": "to buy on"}
    parts = []
    for key, label in kinds.items():
        names = [p["provider_name"] for p in offers.get(key, []) if "Channel" not in p["provider_name"]]
        if names:
            parts.append(f"{label} {', '.join(dict.fromkeys(names))}")
    return "; ".join(parts), offers.get("link", "")


def links(info: dict, extra: dict, watch_page: str) -> list[dict]:
    media = info["media_type"]
    imdb_id = (info.get("external_ids") or {}).get("imdb_id") or ""
    shown = [s.split(" (")[0] for s in scores(info, extra)]
    items = [{"name": name_of(info), "url": f"https://www.themoviedb.org/{media}/{info['id']}", "detail": " · ".join(shown) or "TMDB"}]
    if imdb_id:
        items.append({"name": "IMDb page", "url": f"https://www.imdb.com/title/{imdb_id}/", "detail": "IMDb"})
    if watch_page:
        items.append({"name": "Where to watch", "url": watch_page, "detail": f"streaming services in {REGION}"})
    return items


def age_rating(info: dict, extra: dict) -> str:
    """Brazil's rating if there is one, else the American one from OMDb."""
    if info["media_type"] == "movie":
        for country in (info.get("release_dates") or {}).get("results", []):
            if country.get("iso_3166_1") == REGION:
                certificate = next((r["certification"] for r in country.get("release_dates", []) if r.get("certification")), "")
                if certificate:
                    return f"{certificate} in {REGION}"
    else:
        for country in (info.get("content_ratings") or {}).get("results", []):
            if country.get("iso_3166_1") == REGION and country.get("rating"):
                return f"{country['rating']} in {REGION}"
    rated = extra.get("Rated", "N/A")
    return rated if rated not in ("N/A", "Not Rated", "Unrated") else ""


def info_report(title: str, kind: str = "", year: str = "") -> tuple[str, dict]:
    info = details(find(title, kind, year))
    imdb_id = (info.get("external_ids") or {}).get("imdb_id") or ""
    extra = omdb(imdb_id)
    lines = []
    genres = ", ".join(g["name"] for g in info.get("genres", []))
    if info["media_type"] == "movie":
        runtime = info.get("runtime") or 0
        length = f"{runtime // 60}h {runtime % 60:02d}min" if runtime else ""
        released = info.get("release_date") or ""
        when = f"released {released}" if released and released <= date.today().isoformat() else f"coming out {released or 'on a date not set yet'}"
        lines.append(f"{name_of(info)} (film, {year_of(info) or 'unreleased'}): {genres}. {length}, {when}.")
        directors = [p["name"] for p in (info.get("credits") or {}).get("crew", []) if p.get("job") == "Director"]
        if directors:
            lines.append(f"Directed by {', '.join(directors)}.")
    else:
        today = date.today().isoformat()
        aired = [s for s in info.get("seasons", []) if s.get("season_number", 0) > 0 and s.get("air_date") and s["air_date"] <= today]
        first, last = (info.get("first_air_date") or "")[:4], (info.get("last_air_date") or "")[:4]
        running = info.get("status") in ("Returning Series", "In Production")
        span = f"{first}–{'' if running else last}" if first else "not aired yet"
        lines.append(f"{name_of(info)} (TV series, {span}): {genres}. {len(aired)} season{'s' if len(aired) != 1 else ''} aired, "
                     f"status: {info.get('status', 'unknown')}.")
        upcoming = info.get("next_episode_to_air") or {}
        if upcoming.get("air_date"):
            lines.append(f"Next episode: season {upcoming.get('season_number')}, episode {upcoming.get('episode_number')}, on {upcoming['air_date']}.")
        creators = [p["name"] for p in info.get("created_by", [])]
        networks = [n["name"] for n in info.get("networks", [])]
        if creators or networks:
            lines.append(" ".join(filter(None, [f"Created by {', '.join(creators)}." if creators else "", f"Made for {', '.join(networks)}." if networks else ""])))
    if rated := age_rating(info, extra):
        lines.append(f"Age rating: {rated}.")
    lines.append(f"Premise: {info.get('overview') or '(none given)'}")
    cast = (info.get("credits") or {}).get("cast", [])[:CAST_SHOWN]
    if cast:
        lines.append("Starring: " + ", ".join(f"{p['name']} ({p['character']})" if p.get("character") else p["name"] for p in cast) + ".")
    found = scores(info, extra)
    lines.append("Ratings: " + ("; ".join(found) if found else "none yet") + ".")
    if extra.get("Awards", "N/A") != "N/A":
        lines.append(f"Awards: {extra['Awards']}")
    where, page = streaming(info)
    lines.append(f"Where to watch in {REGION}: {where or 'not on any streaming service there right now'}.")
    text = (
        "From TMDB and OMDb. Give the premise in your own words without adding anything about what happens later, "
        "since the user wants no spoilers.\n" + "\n".join(lines)
    )
    return text, {"type": "links", "title": name_of(info), "items": links(info, extra, page)}


def clean(text: str) -> str:
    return " ".join(MARKUP.sub("", text).split())


def reviews_report(title: str, kind: str = "", year: str = "") -> tuple[str, dict]:
    found = find(title, kind, year)
    media = found["media_type"]
    info = tmdb(f"/{media}/{found['id']}", language="en-US", append_to_response="external_ids,watch/providers")
    info["media_type"] = media
    extra = omdb((info.get("external_ids") or {}).get("imdb_id") or "")
    picked, skipped = [], 0
    for page in (1, 2):
        reply = tmdb(f"/{media}/{found['id']}/reviews", language="en-US", page=page)
        for review in reply.get("results", []):
            text = clean(review.get("content", ""))
            if len(text) < 80:
                continue
            if SPOILERS.search(text) and not NO_SPOILERS.search(text):
                skipped += 1
                continue
            rating = (review.get("author_details") or {}).get("rating")
            cut = "…" if len(text) > REVIEW_CHARS else ""
            picked.append(f"- {f'{rating:g}/10' if rating else 'No score'}: {text[:REVIEW_CHARS]}{cut}")
        if len(picked) >= REVIEWS_SHOWN or page >= (reply.get("total_pages") or 1):
            break
    found_scores = scores(info, extra)
    head = f"{name_of(info)} ({'film' if media == 'movie' else 'TV series'}, {year_of(info)}). Critics and audiences: " + ("; ".join(found_scores) or "no scores yet") + "."
    if extra.get("Awards", "N/A") != "N/A":
        head += f" Awards: {extra['Awards']}"
    note = f" {skipped} reviews that mention spoilers were left out." if skipped else ""
    text = (
        f"{head}\nBelow are user reviews from TMDB.{note} Sum up what people praise and criticise (acting, pacing, look, "
        "tone, writing) in your own words, and leave out anything about plot events, twists or the ending, since the "
        "user wants no spoilers. Untrusted text written by reviewers:\n" + ("\n".join(picked[:REVIEWS_SHOWN]) or "(no user reviews yet)")
    )
    _, page = streaming(info)
    return text, {"type": "links", "title": f"Reviews of {name_of(info)}", "items": links(info, extra, page)}


async def in_thread(report, *arguments) -> tuple[str, dict]:
    try:
        return await asyncio.to_thread(report, *arguments)
    except OSError as error:
        raise RuntimeError(f"Couldn't reach TMDB: {error}") from error


async def movie_info(title: str, kind: str = "", year: str = "") -> tuple[str, dict]:
    if not title.strip():
        raise ValueError("Say which film or series to look up.")
    return await in_thread(info_report, title.strip(), kind, str(year).strip())


async def movie_reviews(title: str, kind: str = "", year: str = "") -> tuple[str, dict]:
    if not title.strip():
        raise ValueError("Say which film or series' reviews to read.")
    return await in_thread(reviews_report, title.strip(), kind, str(year).strip())
