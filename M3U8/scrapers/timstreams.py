from functools import partial
from typing import Any

import httpx
from playwright.async_api import async_playwright

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIM"

CACHE_FILE = Cache(f"{TAG}.json", exp=3_600)

API_URL = "https://api.timstreams.site/main"

BASE_MIRRORS = [
    "https://timstreams.site",
    "https://timstreams.space",
    "https://timstreams.top",
]

SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    8: "American Football",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
    12: "Darts",
    13: "Cricket",
    14: "Cycling",
    15: "Rugby",
    16: "Live Shows",
    17: "Other",
}


async def refresh_api_cache(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    try:
        r = await client.get(API_URL)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{API_URL}": {e}')

        return []

    return r.json()


async def get_events(
    client: httpx.AsyncClient, cached_keys: set[str]
) -> list[dict[str, str]]:
    api_data = await refresh_api_cache(client)

    now = Time.now().timestamp()

    events = []

    for info in api_data:
        if not (category := info.get("category")) or category != "Events":
            continue

        stream_events: list[dict[str, Any]] = info["events"]

        for ev in stream_events:
            name: str = ev["name"]

            logo = ev.get("logo")

            if (genre := ev["genre"]) in {16, 17}:
                continue

            sport = SPORT_GENRES.get(genre, "Live Event")

            streams: list[dict[str, str]] = ev["streams"]

            for stream in streams:
                key = f"[{sport}] {name} ({TAG})"

                if cached_keys & {key}:
                    continue

                if not (url := stream.get("url")):
                    continue

                events.append(
                    {
                        "key": key,
                        "sport": sport,
                        "event": name,
                        "link": url,
                        "logo": logo,
                        "timestamp": now,
                    }
                )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    if not (base_url := await network.get_base(BASE_MIRRORS)):
        log.warning("No working Timstreams mirrors")
        CACHE_FILE.write(cached_urls)
        return

    log.info(f'Scraping from "{base_url}"')

    events = await get_events(client, set(cached_urls.keys()))

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        async with async_playwright() as p:
            browser, context = await network.browser(p)

            for i, ev in enumerate(events, start=1):
                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    context=context,
                    log=log,
                )

                url = await network.safe_process(
                    handler,
                    url_num=i,
                    log=log,
                )

                if url:
                    sport, event, logo, ts, link, key = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["timestamp"],
                        ev["link"],
                        ev["key"],
                    )

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": base_url,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    urls[key] = cached_urls[key] = entry

            await browser.close()

    if new_count := len(cached_urls) - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
