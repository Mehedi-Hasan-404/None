import asyncio
import base64
import re
from collections.abc import KeysView
from functools import partial
from itertools import chain
from urllib.parse import urljoin

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FUTBOLX"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://futbol-x.xyz"

SPORT_URLS = [
    urljoin(BASE_URL, f"api/{sport}.json")
    for sport in [
        # "basketball",
        # "darts",
        "fights",
        "football",
        # "golf",
        "mlb",
        "motorsports",
        "nfl",
        # "nhl",
        # "others",
        # "rugby",
        # "tennis",
        "wrestling",
    ]
]


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    valid_m3u8 = re.compile(r'var\s+hiddenUrl\s+=\s+"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(html_data.text)):
        log.warning(f"URL {url_num}) No M3U8 found")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return base64.b64decode(match[1]).decode("utf-8")


async def get_events(cached_keys: KeysView[str]) -> list[Event]:
    events: list[Event] = []

    tasks = [network.request(url, log=log) for url in SPORT_URLS]

    results = await asyncio.gather(*tasks)

    if not (
        api_data := [
            *chain.from_iterable(r.json().get("streams", {}) for r in results if r)
        ]
    ):
        return events

    now = Time.clean(Time.now())

    for event in api_data:
        if not (streams := event.get("streams")):
            continue

        for event_info in streams:
            if not all(
                values := [
                    event_info.get(k)
                    for k in (
                        "name",
                        "tag",
                        "starts_at",
                        "streams",
                    )
                ]
            ):
                continue

            name, sport, event_time, event_streams = values

            sport = sport.upper() if len(sport) == 3 else sport

            event_dt = Time.from_str(event_time, timezone="MSK")

            if event_dt.date() != now.date():
                continue

            for stream_info in event_streams:
                if not (url := stream_info.get("url")):
                    continue

                feed = stream_info["title"]

                if f"[{sport}] {name} | {feed} ({TAG})" in cached_keys:
                    continue

                events.append(
                    Event(
                        sport=sport,
                        name=f"{name} | {feed}",
                        link=url,
                        timestamp=now.timestamp(),
                    )
                )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["source"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            source = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "source": source,
                "logo": logo,
                "refer": ev.link,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
            }

            cached_urls[key] = entry

            if source:
                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
