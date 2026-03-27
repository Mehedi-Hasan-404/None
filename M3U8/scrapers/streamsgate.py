import asyncio
from functools import partial
from itertools import chain
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Browser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMSGATE"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

BASE_URL = "https://streamsgates.io"

SPORT_URLS = [
    urljoin(BASE_URL, f"data/{sport}.json")
    for sport in [
        # "cfb",
        "mlb",
        "nba",
        # "nfl",
        "nhl",
        "soccer",
        "ufc",
    ]
]


def get_event(t1: str, t2: str) -> str:
    match t1:
        case "RED ZONE":
            return "NFL RedZone"

        case "TBD":
            return "TBD"

        case _:
            return f"{t1.strip()} vs {t2.strip()}"


async def refresh_api_cache(now_ts: float) -> list[dict[str, Any]]:
    tasks = [network.request(url, log=log) for url in SPORT_URLS]

    results = await asyncio.gather(*tasks)

    if not (data := [*chain.from_iterable(r.json() for r in results if r)]):
        return [{"timestamp": now_ts}]

    for ev in data:
        ev["ts"] = ev.pop("timestamp")

    data[-1]["timestamp"] = now_ts

    return data


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = await refresh_api_cache(now.timestamp())

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-1)
    end_dt = now.delta(minutes=5)

    for stream_group in api_data:
        date = stream_group.get("time")

        sport = stream_group.get("league")

        t1, t2 = stream_group.get("away"), stream_group.get("home")

        if not (t1 and t2):
            continue

        event = get_event(t1, t2)

        if not (date and sport):
            continue

        if f"[{sport}] {event} ({TAG})" in cached_keys:
            continue

        event_dt = Time.from_str(date, timezone="UTC")

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (streams := stream_group.get("streams")):
            continue

        if not (url := streams[0].get("url")):
            continue

        events.append(
            {
                "sport": sport,
                "event": event,
                "link": url,
                "timestamp": event_dt.timestamp(),
            }
        )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://instreams.click/",
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1

                        entry["url"] = url.split("&e")[0]

                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
