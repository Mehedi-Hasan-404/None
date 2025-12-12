import re
from functools import partial
from typing import Any
from urllib.parse import urljoin

import httpx
from playwright.async_api import async_playwright

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

CACHE_FILE = Cache("strmd.json", exp=10_800)

API_FILE = Cache("strmd-api.json", exp=28_800)

MIRRORS = [
    "https://streami.su",
    "https://streamed.st",
    "https://streamed.pk",
]

TAG = "STRMD"


def fix_sport(s: str) -> str:
    if "-" in s:
        return " ".join(i.capitalize() for i in s.split("-"))

    elif s == "fight":
        return "Fight (UFC/Boxing)"

    return s.capitalize() if len(s) >= 4 else s.upper()


async def refresh_api_cache(
    client: httpx.AsyncClient,
    url: str,
    now_ts: float,
) -> list[dict[str, Any]]:

    log.info("Refreshing API cache")

    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')

        return []

    if not (data := r.json()):
        return []

    data[-1]["timestamp"] = now_ts

    return data


async def get_events(
    client: httpx.AsyncClient,
    url: str,
    cached_keys: set[str],
) -> list[dict[str, str]]:

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        api_data = await refresh_api_cache(
            client,
            urljoin(url, "api/matches/all-today"),
            now.timestamp(),
        )

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)
    pattern = re.compile(r"[\n\r]+|\s{2,}")

    for event in api_data:
        if (category := event.get("category")) == "other":
            continue

        if not (ts := event["date"]):
            continue

        start_ts = int(f"{ts}"[:-3])

        event_dt = Time.from_ts(start_ts)

        if not start_dt <= event_dt <= end_dt:
            continue

        sport = fix_sport(category)

        parts = pattern.split(event["title"].strip())
        name = " | ".join(p.strip() for p in parts if p.strip())

        logo = urljoin(url, poster) if (poster := event.get("poster")) else None

        key = f"[{sport}] {name} ({TAG})"

        if cached_keys & {key}:
            continue

        sources: list[dict[str, str]] = event["sources"]

        if not sources:
            continue

        skip_types = {"alpha", "bravo"}
        valid_sources = [d for d in sources if d.get("source") not in skip_types]

        if not valid_sources:
            continue

        srce = valid_sources[0]

        source_type = srce.get("source")
        stream_id = srce.get("id")

        if not (source_type and stream_id):
            continue

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": f"https://embedsports.top/embed/{source_type}/{stream_id}/1",
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

    if not (base_url := await network.get_base(MIRRORS)):
        log.warning("No working STRMD mirrors")
        CACHE_FILE.write(cached_urls)
        return

    log.info(f'Scraping from "{base_url}"')

    events = await get_events(
        client,
        base_url,
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
                        "base": "https://embedsports.top/",
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
