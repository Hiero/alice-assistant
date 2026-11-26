"""Web search that reads the top pages, not just their snippets.

Snippets are a sentence or two, which can't answer "who are the characters in X". So the top few
pages are fetched, and from each the part that best matches the question is kept, within a budget
the small local model can handle.
"""

import asyncio
import math
import re
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from ddgs import DDGS
from ddgs.exceptions import DDGSException

from alice.debuglog import log

RESULTS = 6
PAGES_READ = 3
PAGE_BUDGET = 3000  # Characters kept from each page.
SNIPPET_CHARS = 300
FETCH_TIMEOUT = 8
# Engines fail at random, often with "no results", so try several in turn.
BACKENDS = ("auto", "google", "bing", "yahoo", "duckduckgo")
# Pages that rarely have readable text: videos, social media and shops.
SKIPPED_SITES = ("youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "x.com", "twitter.com", "reddit.com/media")
STOPWORDS = set("""
a an the of in on at to for from by with and or is are was were be been what who whom which when where why how
do does did list all any some about tell me my i you your it its this that these those there their game games
o a os as de da do das dos em no na nos nas um uma uns umas e ou que quem qual quais quando onde como por para com
""".split())


def search(query: str) -> list[dict]:
    last_error = None
    for backend in BACKENDS:
        try:
            results = DDGS(timeout=10).text(query, safesearch="moderate", max_results=RESULTS, backend=backend)
        except DDGSException as error:
            last_error = error
            continue
        if results:
            log.debug("web search %r answered by %s", query, backend)
            return results
    raise RuntimeError(f"Web search found nothing ({last_error}).")


def terms(text: str) -> list[str]:
    words = re.findall(r"\w+", text.casefold())
    # A light stem, so "characters" also finds "character".
    return [w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in STOPWORDS and len(w) > 1]


JUNK_LINE = re.compile(r"\.(png|jpe?g|webp|gif|svg)$|^(close|×|menu|log in|sign in|share|tweet)$", re.IGNORECASE)


def is_content(line: str) -> bool:
    """A sentence or a table row, as opposed to a menu entry or a button."""
    return len(line) >= 50 or "│" in line or line.count("|") >= 2


def excerpt(page: str, query: str, budget: int = PAGE_BUDGET) -> str:
    """The stretch of a page that best answers the query.

    Every window of consecutive lines that fits the budget is scored by the query's words, each
    weighted by how rare it is on the page (the game's name is everywhere; "characters" isn't), plus
    how much real content it holds, so a character table beats the menu that sits beside its heading.
    """
    seen, lines = set(), []
    for raw in page.splitlines():
        line = " ".join(raw.split())
        if not line or JUNK_LINE.search(line) or line in seen:
            continue
        seen.add(line)
        lines.append(line)
    wanted = set(terms(query))
    if not lines:
        return ""
    line_terms = [set(terms(line)) for line in lines]
    weight = {t: math.log((len(lines) + 1) / (1 + sum(t in lt for lt in line_terms))) for t in wanted}
    hits = [sum(weight[t] for t in wanted & lt) for lt in line_terms]
    content = [len(line) if is_content(line) else 0 for line in lines]
    best, best_value, end = (0, 0), -1.0, 0
    size = value_terms = value_content = 0
    # A sliding window: grow the end while it fits, then score, then drop the first line.
    for start in range(len(lines)):
        while end < len(lines) and size + len(lines[end]) + 1 <= budget:
            size += len(lines[end]) + 1
            value_terms += hits[end]
            value_content += content[end]
            end += 1
        value = 3 * value_terms + value_content / 150
        if value > best_value:
            best, best_value = (start, max(end, start + 1)), value
        if end > start:
            size -= len(lines[start]) + 1
            value_terms -= hits[start]
            value_content -= content[start]
        else:
            end = start + 1
    return "\n".join(lines[best[0]:best[1]])[:budget]


def read_page(url: str) -> str:
    content = DDGS(timeout=FETCH_TIMEOUT).extract(url, fmt="text_plain")["content"]
    return content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content)


def search_and_read(query: str) -> tuple[str, list[dict]]:
    """The text for the model, and the pages it came from for the user to open."""
    results = search(query)
    readable = [r for r in results if not any(site in r.get("href", "") for site in SKIPPED_SITES)][:PAGES_READ]
    with ThreadPoolExecutor(max_workers=PAGES_READ) as pool:
        futures = {r["href"]: pool.submit(read_page, r["href"]) for r in readable}
        pages = {}
        for url, future in futures.items():
            try:
                pages[url] = future.result(timeout=FETCH_TIMEOUT + 4)
            except Exception as error:  # noqa: BLE001 - an unreadable page just falls back to its snippet
                log.debug("couldn't read %s: %s", url, error)
    sections, sources = [], []
    for r in results:
        url = r.get("href", "")
        text = excerpt(pages[url], query) if url in pages else ""
        body = text or (r.get("body") or "")[:SNIPPET_CHARS]
        label = "page excerpt" if text else "search snippet"
        sections.append(f"## {r.get('title')} ({url}), {label}:\n{body}")
        site = urlparse(url).hostname or ""
        # Only ordinary web addresses become links the user can click.
        if url.startswith(("https://", "http://")) and site:
            sources.append({"name": " ".join(str(r.get("title") or site).split())[:90], "url": url, "detail": site.removeprefix("www.")})
    return "\n\n".join(sections), sources


async def web_search(query: str) -> tuple[str, dict]:
    query = " ".join(query.split())
    if not query:
        raise ValueError("The search needs a query.")
    text, sources = await asyncio.to_thread(search_and_read, query)
    return (
        "Untrusted text from web pages. Answer from it, naming the website; if the pages disagree or don't "
        "answer the question, say so instead of guessing. The pages are shown to the user as clickable sources "
        "under your answer.\n\n" + text,
        {"type": "links", "title": "Sources", "items": sources},
    )
