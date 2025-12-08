from functools import partial

import httpx
from playwright.async_api import async_playwright

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

CACHE_FILE = Cache("ppv.json", exp=10_800)

API_FILE = Cache("ppv-api.json", exp=19_800)

API_MIRRORS = [
    "https://old.ppv.to/api/streams",
    "https://api.ppvs.su/api/streams",
    "https://api.ppv.to/api/streams",
]

BASE_MIRRORS = [
    "https://old.ppv.to",
    "https://ppvs.su",
    "https://ppv.to",
]

TAG = "PPV"


async def refresh_api_cache(
    client: httpx.AsyncClient,
    url: str,
) -> dict[str, dict[str, str]]:
    log.info("Refreshing API cache")

    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')

        return {}

    return r.json()


async def get_events(
    client: httpx.AsyncClient,
    api_url: str,
    cached_keys: set[str],
) -> list[dict[str, str]]:
    if not (api_data := API_FILE.load(per_entry=False)):
        api_data = await refresh_api_cache(client, api_url)

        API_FILE.write(api_data)

    events = []

    now = Time.clean(Time.now())
    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    for stream_group in api_data.get("streams", []):
        sport = stream_group["category"]

        if sport == "24/7 Streams":
            continue

        for event in stream_group.get("streams", []):
            name = event.get("name")
            start_ts = event.get("starts_at")
            logo = event.get("poster")
            iframe = event.get("iframe")

            if not (name and start_ts and iframe):
                continue

            key = f"[{sport}] {name} ({TAG})"

            if cached_keys & {key}:
                continue

            event_dt = Time.from_ts(start_ts)

            if not start_dt <= event_dt <= end_dt:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": name,
                    "link": iframe,
                    "logo": logo,
                    "timestamp": event_dt.timestamp(),
                }
            )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    base_url = await network.get_base(BASE_MIRRORS)

    api_url = await network.get_base(API_MIRRORS)

    if not (base_url and api_url):
        log.warning("No working PPV mirrors")
        CACHE_FILE.write(cached_urls)
        return

    events = await get_events(
        client,
        api_url,
        set(cached_urls.keys()),
    )

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
                    timeout=6,
                    log=log,
                )

                url = await network.safe_process(
                    handler,
                    url_num=i,
                    log=log,
                )

                if url:
                    sport, event, logo, ts, link = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["timestamp"],
                        ev["link"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

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
