"""The morning summary shown on the first open of the day: today's weather, and wishlist games that came out or went on sale."""

import asyncio
import os

from alice.debuglog import log

PARTS = [p.strip() for p in os.environ.get("ALICE_MORNING", "weather,wishlist").lower().split(",") if p.strip() not in ("", "off", "none")]
TIMEOUT_SECONDS = 15


def weather_part() -> str:
    from alice.weather import home_city, today_line

    city = home_city()
    return today_line(city) if city else ""


def wishlist_part() -> str:
    from alice.steamlibrary import wishlist_alert

    alert = wishlist_alert()
    return f"🎮 {alert}" if alert else ""


async def morning_summary() -> str:
    """Each part is optional and fails quietly: a missing city or an offline Steam shouldn't spoil the morning."""
    makers = {"weather": weather_part, "wishlist": wishlist_part}
    lines = []
    for name in PARTS:
        if name not in makers:
            continue
        try:
            line = await asyncio.wait_for(asyncio.to_thread(makers[name]), TIMEOUT_SECONDS)
        except Exception:  # noqa: BLE001 - any failure just leaves this line out
            log.debug("morning summary part %s failed", name, exc_info=True)
            continue
        if line:
            lines.append(line)
    return "\n".join(lines)
