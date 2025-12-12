from functools import partial

import httpx
from playwright.async_api import async_playwright

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

CACHE_FILE = Cache("lotus.json", exp=5_400)

API_CACHE = Cache("lotus-api.json", exp=28_800)

BASE_URL = "https://lotusgamehd.xyz/api-event.php"

TAG = "LOTUS"


def fix_league(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split()) if len(s) > 5 else s.upper()


async def refresh_api_cache(
    client: httpx.AsyncClient,
    url: str,
    now_ts: float,
) -> dict[str, dict[str, str]]:
    log.info("Refreshing API cache")

    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')

        return {}

    if not (data := r.json()):
        return {}

    data["timestamp"] = now_ts

    return data


async def get_events(
    client: httpx.AsyncClient,
    url: str,
    cached_keys: set[str],
) -> list[dict[str, str]]:
    now = Time.now()

    if not (api_data := API_CACHE.load(per_entry=False)):
        api_data = await refresh_api_cache(
            client,
            url,
            now.timestamp(),
        )

        API_CACHE.write(api_data)

    events = []

    for info in api_data.get("days", []):
        event_dt = Time.from_str(info["day_et"], timezone="ET")

        if now.date() != event_dt.date():
            continue

        for event in info["items"]:
            event_league = event["league"]

            if event_league == "channel tv":
                continue

            event_streams: list[dict[str, str]] = event["streams"]

            if not (event_link := event_streams[0].get("link")):
                continue

            sport = fix_league(event_league)
            event_name = event["title"]

            key = f"[{sport}] {event_name} ({TAG})"

            if cached_keys & {key}:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
                    "link": event_link,
                }
            )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(
        client,
        BASE_URL,
        set(cached_urls.keys()),
    )

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.now().timestamp()

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
                    sport, event, link = (
                        ev["sport"],
                        ev["event"],
                        ev["link"],
                    )

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    key = f"[{sport}] {event} ({TAG})"

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://vividmosaica.com/",
                        "timestamp": now,
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
