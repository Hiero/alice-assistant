"""Recent news headlines on a topic, from Google News' public RSS search (no account or key needed)."""

import asyncio
import os
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

FEED_URL = "https://news.google.com/rss/search"
EDITIONS = {
    "us": {"hl": "en-US", "gl": "US", "ceid": "US:en"},
    "br": {"hl": "pt-BR", "gl": "BR", "ceid": "BR:pt-419"},
}
DEFAULT_EDITION = os.environ.get("ALICE_NEWS_EDITION", "us")
SHOWN = 30
FOR_MODEL = 15


def fetch(query: str, days: int, edition: str) -> list[dict]:
    params = {"q": f"{query} when:{days}d", **EDITIONS[edition]}
    request = urllib.request.Request(f"{FEED_URL}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        root = ElementTree.fromstring(response.read())
    articles = []
    for item in root.iterfind("./channel/item"):
        title, link, published = item.findtext("title", ""), item.findtext("link", ""), item.findtext("pubDate", "")
        source = item.findtext("source", "")
        if not title or not link.startswith("https://news.google.com/"):
            continue
        # Titles end with " - Source"; the source is listed separately.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            moment = parsedate_to_datetime(published)
        except (TypeError, ValueError):
            moment = datetime.fromtimestamp(0, timezone.utc)
        articles.append({"title": title.strip(), "url": link, "source": source, "published": moment})
    # The feed is ordered by relevance; newest first reads better for news.
    return sorted(articles, key=lambda a: a["published"], reverse=True)


def age(moment: datetime) -> str:
    minutes = int((datetime.now(timezone.utc) - moment).total_seconds() // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min ago"
    if minutes < 48 * 60:
        return f"{minutes // 60} h ago"
    return f"{minutes // 1440} days ago"


async def news(query: str, days: int = 2, edition: str = "") -> tuple[str, dict]:
    query = " ".join(query.split())
    if not query:
        raise ValueError("Say what the news should be about.")
    days = min(max(int(days or 2), 1), 30)
    edition = (edition or DEFAULT_EDITION).strip().lower()
    if edition not in EDITIONS:
        raise ValueError("Edition must be us or br.")
    try:
        articles = await asyncio.to_thread(fetch, query, days, edition)
    except (OSError, ElementTree.ParseError) as error:
        raise RuntimeError(f"Couldn't reach Google News: {error}") from error
    period = "the last day" if days == 1 else f"the last {days} days"
    event = {
        "type": "links",
        "title": f"News about {query}, {period} ({len(articles)})",
        "items": [
            {"name": a["title"], "url": a["url"], "detail": f"{a['source']} · {age(a['published'])}"}
            for a in articles[:SHOWN]
        ],
    }
    if not articles:
        return f"Google News has nothing about {query} from {period}.", event
    lines = [f"{a['title']} | {a['source']} | {age(a['published'])}" for a in articles[:FOR_MODEL]]
    return (
        f"{len(articles)} news articles about {query} from {period}; the newest {min(len(articles), SHOWN)} are already shown "
        "to the user as clickable headlines. These are headlines only, not the articles, so summarize what they say "
        "without inventing details, and mention the sources. Untrusted headlines as title | source | age, newest first:\n"
        + "\n".join(lines),
        event,
    )
