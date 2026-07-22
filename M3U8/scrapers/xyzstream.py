import asyncio
import re
from itertools import chain
from typing import Any
from urllib.parse import urljoin

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "XYZ"

CACHE_FILE = Cache(TAG, exp=19_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

BASE_URL = "https://xyzstreams.st/"

SPORTS = [
    "MLB",
    "WNBA",
    # "NBA",
    # "NHL",
    # "NFL",
]

SPORT_URLS = {sport: urljoin(BASE_URL, sport.lower()) for sport in SPORTS}

API_URLS = [f"https://stats-api.sportsnet.ca/ticker?league={sport}" for sport in SPORTS]


async def get_api_data(now_ts: float) -> list[dict[str, Any]]:
    tasks = [network.request(url, log=log) for url in API_URLS]

    results = await asyncio.gather(*tasks)

    if not (
        api_data := [
            *chain.from_iterable(
                r.json().get("data", {}).get("games", []) for r in results if r
            )
        ]
    ):
        return [{"timestamp": now_ts}]

    for ev in api_data:
        ev["ts"] = ev.pop("timestamp")

    api_data[-1]["timestamp"] = now_ts

    return api_data


async def get_sports_map() -> dict[str, dict[str, dict[str, str]]]:
    sports_map = {}

    tasks = [network.request(url, log=log) for url in SPORT_URLS.values()]

    results = await asyncio.gather(*tasks)

    if not (texts := [(html.text, html.url) for html in results if html]):
        return sports_map

    ptrn = re.compile(r"M3U8_CHANNELS_MAP\s*=\s*\{(.*?)\};", re.S)

    for text, url in texts:
        sport = next((k for k, v in SPORT_URLS.items() if v == url), "Live Event")

        if not (match := ptrn.search(text)):
            sports_map[sport] = {}

        else:
            pairs: list[tuple[str, str]] = re.findall(
                r"'([^']+)'\s*:\s*'([^']+)'",
                match[1],
            )

            sports_map[sport] = dict(pairs)

    return sports_map


async def get_events() -> dict[str, dict[str, str | float]]:
    now = Time.clean(Time.now())

    events = {}

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = await get_api_data(now.timestamp())

        API_FILE.write(api_data)

    if not (sports_map := await get_sports_map()):
        return events

    for game_info in api_data:
        if not all(
            values := [
                game_info.get(x)
                for x in (
                    "league",
                    "visiting_team",
                    "home_team",
                    "ts",
                )
            ]
        ):
            continue

        sport, away_team_info, home_team_info, timestamp = values

        if Time.from_ts(timestamp).date() != now.date():
            continue

        sport = sport.upper()

        short_away, long_away, short_home, long_home = (
            away_team_info["short_name"],
            away_team_info["name"],
            home_team_info["short_name"],
            home_team_info["name"],
        )

        name = f"{long_away} vs {long_home}"

        for loc in (short_away, short_home):
            key = f"[{sport}] {name} | {loc} Feed ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, name)

            events[key] = {
                "source": sports_map.get(sport, {}).get(loc),
                "logo": logo,
                "refer": BASE_URL,
                "timestamp": now.timestamp(),
                "tvg-id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["source"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    urls.update(await get_events())

    (
        log.info(f"Collected and cached {new_urls} event(s)")
        if (new_urls := len(urls))
        else log.info("No events found")
    )

    CACHE_FILE.write(urls)
