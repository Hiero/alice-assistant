"""Detailed weather and forecasts from Open-Meteo, which needs no account or key."""

import asyncio
import json
import os
import urllib.parse
import re
import urllib.request
from datetime import datetime

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
HOME = os.environ.get("ALICE_WEATHER_LOCATION", "").strip()

CURRENT = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m", "dew_point_2m", "weather_code", "cloud_cover",
    "precipitation", "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "pressure_msl", "uv_index", "visibility", "is_day",
]
HOURLY = ["temperature_2m", "apparent_temperature", "precipitation_probability", "precipitation", "weather_code", "wind_speed_10m"]
DAILY = [
    "weather_code", "temperature_2m_max", "temperature_2m_min", "apparent_temperature_max", "apparent_temperature_min",
    "precipitation_sum", "precipitation_probability_max", "precipitation_hours", "wind_speed_10m_max", "wind_gusts_10m_max",
    "wind_direction_10m_dominant", "uv_index_max", "sunrise", "sunset",
]
CONDITIONS = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
    51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "light freezing drizzle", 57: "freezing drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain", 66: "light freezing rain", 67: "freezing rain",
    71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers", 81: "showers",
    82: "violent showers", 85: "light snow showers", 86: "snow showers", 95: "thunderstorm",
    96: "thunderstorm with light hail", 99: "thunderstorm with hail",
}
PLACEHOLDERS = {
    "here", "home", "my home", "my city", "current location", "my location", "current city", "user location",
    "user's location", "user's city", "user's home", "local", "nearby", "unknown", "none",
}
ICONS = [
    ((0,), "☀️"), ((1,), "🌤️"), ((2,), "⛅"), ((3,), "☁️"), ((45, 48), "🌫️"), ((51, 53, 55, 56, 57, 80, 81, 82), "🌦️"),
    ((61, 63, 65, 66, 67), "🌧️"), ((71, 73, 75, 77, 85, 86), "🌨️"), ((95, 96, 99), "⛈️"),
]
CARD_DAYS = 7
DIRECTIONS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()


def trim_days(text: str, days: int) -> str:
    """Keep the daily lines the model asked for, though a week was fetched for the card."""
    head, marker, daily = text.partition("\nDaily forecast for ")
    if not marker:
        return text
    rows = daily.splitlines()[1:]
    return f"{head}{marker}{days} day{'s' if days > 1 else ''}:\n" + "\n".join(rows[:days])


def get(url: str, params: dict) -> dict:
    request = urllib.request.Request(f"{url}?{urllib.parse.urlencode(params)}", headers={"User-Agent": "alice-assistant"})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


def find_place(name: str) -> dict:
    """A place by name. "City, region" or "City, country" picks among places with the same name."""
    city, _, hint = name.partition(",")
    results = get(GEOCODE_URL, {"name": city.strip(), "count": 10, "language": "en"}).get("results") or []
    hint = hint.strip().casefold()
    if hint:
        matching = [
            r for r in results
            if hint in " ".join(str(r.get(k, "")) for k in ("admin1", "admin2", "country", "country_code")).casefold()
        ]
        results = matching or results
    if not results:
        raise ValueError(f"I couldn't find a place called {name}.")
    return results[0]


def compass(degrees: float | None) -> str:
    return "" if degrees is None else DIRECTIONS[round(degrees / 22.5) % 16]


def condition(code: int | None) -> str:
    return CONDITIONS.get(code, "unknown") if code is not None else "unknown"


def air_quality(aqi: float | None) -> str:
    if aqi is None:
        return "unknown"
    for limit, label in ((50, "good"), (100, "moderate"), (150, "unhealthy for sensitive people"), (200, "unhealthy"), (300, "very unhealthy")):
        if aqi <= limit:
            return f"{label} (US AQI {aqi:.0f})"
    return f"hazardous (US AQI {aqi:.0f})"


def uv_level(uv: float | None) -> str:
    if uv is None:
        return "unknown"
    label = "low" if uv < 3 else "moderate" if uv < 6 else "high" if uv < 8 else "very high" if uv < 11 else "extreme"
    return f"{uv:.0f} ({label})"


def icon(code: int | None) -> str:
    return next((symbol for codes, symbol in ICONS if code in codes), "🌡️")


def report(place: dict, days: int) -> tuple[str, dict]:
    where = {"latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto"}
    data = get(FORECAST_URL, {
        **where, "current": ",".join(CURRENT), "hourly": ",".join(HOURLY), "daily": ",".join(DAILY),
        "forecast_days": days, "wind_speed_unit": "kmh",
    })
    try:
        air = get(AIR_URL, {**where, "current": "us_aqi,pm2_5"}).get("current", {})
    except OSError:
        air = {}
    now, hourly, daily = data["current"], data["hourly"], data["daily"]
    name = ", ".join(str(p) for p in (place.get("name"), place.get("admin1"), place.get("country")) if p)
    lines = [
        f"Weather for {name}. Local time there: {now['time'].replace('T', ' ')}.",
        "",
        "Now: " + "; ".join([
            f"{condition(now.get('weather_code'))}{'' if now.get('is_day') else ' (night)'}",
            f"{now['temperature_2m']:.0f}°C, feels like {now['apparent_temperature']:.0f}°C",
            f"humidity {now['relative_humidity_2m']}%, dew point {now['dew_point_2m']:.0f}°C",
            f"wind {now['wind_speed_10m']:.0f} km/h from {compass(now.get('wind_direction_10m'))}, gusts {now['wind_gusts_10m']:.0f} km/h",
            f"cloud cover {now['cloud_cover']}%",
            f"rain in the last hour {now['precipitation']} mm",
            f"pressure {now['pressure_msl']:.0f} hPa",
            f"UV {uv_level(now.get('uv_index'))}",
            f"visibility {now['visibility'] / 1000:.0f} km",
            f"air quality {air_quality(air.get('us_aqi'))}",
        ]) + ".",
        "",
        "Next 24 hours, every 3 hours (time: condition, temperature, feels like, chance of rain, rain, wind):",
    ]
    # The hourly series starts at midnight today, so begin at the current hour.
    start = next((i for i, t in enumerate(hourly["time"]) if t >= now["time"][:13]), 0)
    for i in range(start, min(start + 24, len(hourly["time"])), 3):
        lines.append(
            f"{hourly['time'][i][11:16]}: {condition(hourly['weather_code'][i])}, {hourly['temperature_2m'][i]:.0f}°C, "
            f"feels {hourly['apparent_temperature'][i]:.0f}°C, {hourly['precipitation_probability'][i]}% rain chance, "
            f"{hourly['precipitation'][i]} mm, wind {hourly['wind_speed_10m'][i]:.0f} km/h"
        )
    lines += ["", f"Daily forecast for {days} day{'s' if days > 1 else ''}:"]
    for i, day in enumerate(daily["time"]):
        weekday = datetime.fromisoformat(day).strftime("%A")
        label = "Today" if i == 0 else "Tomorrow" if i == 1 else weekday
        lines.append(
            f"{label} {day} ({weekday}): {condition(daily['weather_code'][i])}; "
            f"{daily['temperature_2m_min'][i]:.0f} to {daily['temperature_2m_max'][i]:.0f}°C "
            f"(feels {daily['apparent_temperature_min'][i]:.0f} to {daily['apparent_temperature_max'][i]:.0f}°C); "
            f"{daily['precipitation_probability_max'][i]}% rain chance, {daily['precipitation_sum'][i]} mm over {daily['precipitation_hours'][i]:.0f} h; "
            f"wind up to {daily['wind_speed_10m_max'][i]:.0f} km/h from {compass(daily['wind_direction_10m_dominant'][i])}, "
            f"gusts {daily['wind_gusts_10m_max'][i]:.0f} km/h; UV {uv_level(daily['uv_index_max'][i])}; "
            f"sunrise {daily['sunrise'][i][11:]}, sunset {daily['sunset'][i][11:]}"
        )
    card = {
        "type": "weather",
        "place": place.get("name", ""),
        "now": f"{icon(now.get('weather_code'))} {now['temperature_2m']:.0f}°C, {condition(now.get('weather_code'))}",
        "days": [
            {
                "label": "Today" if i == 0 else datetime.fromisoformat(day).strftime("%a"),
                "icon": icon(daily["weather_code"][i]),
                "high": round(daily["temperature_2m_max"][i]),
                "low": round(daily["temperature_2m_min"][i]),
                "rain": daily["precipitation_probability_max"][i] or 0,
            }
            for i, day in enumerate(daily["time"][:CARD_DAYS])
        ],
    }
    return "\n".join(lines), card


def forecast_days(asked: int | str | None) -> int:
    """The days to fetch, counting today. Models often count only the days after today ("Sunday" on a
    Monday becomes 6), so one more is always added; and at least three, so "tomorrow" is always covered."""
    return min(max(int(asked or 3) + 1, 3), 16)


async def weather(location: str = "", days: int = 3) -> tuple[str, dict]:
    place_name = " ".join(location.split())
    # Small models fill in placeholders instead of leaving the place empty.
    if place_name.casefold().replace("_", " ") in PLACEHOLDERS:
        place_name = ""
    place_name = place_name or HOME
    if not place_name:
        raise ValueError(
            "The user didn't name a place and their home city isn't known. Don't guess a city: ask which city "
            "they mean, and offer to remember it as their home city."
        )
    days = forecast_days(days)
    try:
        place = await asyncio.to_thread(find_place, place_name)
        # The card shows a week even when fewer days were asked for.
        text, card = await asyncio.to_thread(report, place, max(days, CARD_DAYS))
        text = trim_days(text, days)
    except (OSError, KeyError) as error:
        raise RuntimeError(f"Couldn't get the weather: {error}") from error
    return (
        text + "\n\nA small forecast card for the week is shown to the user. Answer what the user asked from this, in a few sentences: the most useful parts, like whether to "
        "take an umbrella or sunscreen, not every number. Give more detail only if they ask for it.",
        card,
    )


def home_city() -> str:
    """ALICE_WEATHER_LOCATION, or the home city she was told to remember."""
    if HOME:
        return HOME
    from alice.tools import read_memories

    for fact in read_memories():
        if match := re.search(r"home (?:city|town) is (.+?)\.?$", fact, re.IGNORECASE):
            return match.group(1)
    return ""


def today_line(place_name: str) -> str:
    """One line for the morning summary."""
    place = find_place(place_name)
    data = get(FORECAST_URL, {
        "latitude": place["latitude"], "longitude": place["longitude"], "timezone": "auto", "forecast_days": 1,
        "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,uv_index_max",
    })["daily"]
    code, rain = data["weather_code"][0], data["precipitation_probability_max"][0] or 0
    advice = " Take an umbrella!" if rain >= 60 else " Sunscreen would be wise." if (data["uv_index_max"][0] or 0) >= 8 else ""
    return (
        f"{icon(code)} {place['name']} today: {condition(code)}, {data['temperature_2m_min'][0]:.0f} to "
        f"{data['temperature_2m_max'][0]:.0f}°C, {rain}% chance of rain.{advice}"
    )
