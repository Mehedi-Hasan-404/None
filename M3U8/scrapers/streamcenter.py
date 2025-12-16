from functools import partial

import httpx
from playwright.async_api import async_playwright

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)

API_FILE = Cache(f"{TAG.lower()}-api.json", exp=28_800)

BASE_URL = "https://backendstreamcenter.youshop.pro:488/api/Parties"


CATEGORIES = {
    4: "Basketball",
    9: "Football",
    13: "Baseball",
    14: "American Football",
    15: "Motor Sport",
    16: "Hockey",
    17: "Fight MMA",
    18: "Boxing",
    19: "NCAA Sports",
    20: "WWE",
    21: "Tennis",
}


async def refresh_api_cache(
    client: httpx.AsyncClient, now_ts: float
) -> list[dict[str, str | int]]:
    log.info("Refreshing API cache")

    try:
        r = await client.get(BASE_URL, params={"pageNumber": 1, "pageSize": 500})
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{r.url}": {e}')

        return []

    if not (data := r.json()):
        return []

    data[-1]["timestamp"] = now_ts

    return data


async def get_events(
    client: httpx.AsyncClient,
    cached_keys: set[str],
) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        api_data = await refresh_api_cache(client, now.timestamp())

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=10)

    for stream_group in api_data:
        category_id: int = stream_group.get("categoryId")

        name: str = stream_group.get("gameName")

        iframe: str = stream_group.get("videoUrl")

        event_time: str = stream_group.get("beginPartie")

        if not (name and category_id and iframe and event_time):
            continue

        event_dt = Time.from_str(event_time, timezone="CET")

        if not start_dt <= event_dt <= end_dt:
            continue

        if not (sport := CATEGORIES.get(category_id)):
            continue

        key = f"[{sport}] {name} ({TAG})"

        if cached_keys & {key}:
            continue

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": iframe.replace("<", "?", count=1),
                "timestamp": event_dt.timestamp(),
            }
        )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://streamcenter.xyz"')

    events = await get_events(client, set(cached_urls.keys()))

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        async with async_playwright() as p:
            browser, context = await network.browser(p, browser="brave")

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
                    sport, event, ts, link = (
                        ev["sport"],
                        ev["event"],
                        ev["timestamp"],
                        ev["link"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://streamcenter.xyz",
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
