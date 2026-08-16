import json
import re
from dataclasses import dataclass
from functools import partial
from typing import Any
from urllib.parse import quote, urlencode, urljoin

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMFREE"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

BASE_URL = "https://streamfree.top"


@dataclass(kw_only=True, slots=True)
class STFEvent(Event):
    link: str | None = None
    logo: str | None = None
    category: str
    stream_key: str


async def process_event(
    stream_key: str,
    category: str,
    url_num: int,
) -> str | None:

    if not (
        quality_data := await network.request(
            urljoin(BASE_URL, f"api/stream-status/{stream_key}"),
            url_num,
            log=log,
        )
    ):
        return

    quality_info: dict[str, str | Any] = quality_data.json()

    if not quality_info.get("available"):
        log.warning(f"URL {url_num}) Stream is unavailable.")
        return

    if not (
        available_quals := sorted(
            (q for q, ok in quality_info.get("qualities", {}).items() if ok),
            key=lambda q: int(q.rstrip("p")),
            reverse=True,
        )
    ):
        if not (
            available_quals := sorted(
                (q for q, ok in quality_info.get("qualities2", {}).items() if ok),
                key=lambda q: int(q.rstrip("p")),
                reverse=True,
            )
        ):
            log.warning(f"URL {url_num}) No available qualities found.")
            return

    if not (
        stream_data := await network.request(
            urljoin(BASE_URL, f"embed/{category}/{stream_key}"),
            url_num,
            log=log,
        )
    ):
        return

    ptrn = re.compile(r"_0x\s+=\s+(.*?);", re.S)

    if not (match := ptrn.search(stream_data.text)):
        log.warning(f"URL {url_num}) Unable to find stream information.")
        return

    m3u_info: dict[str, dict[str, Any]] = json.loads(match[1])[available_quals[0]]

    query = urlencode(m3u_info)

    log.info(f"URL {url_num}) Captured M3U8")

    return urljoin(
        BASE_URL,
        f"live/{stream_key}{available_quals[-1]}/index.m3u8?{query}",
    )


async def get_events(cached_keys: list[str]) -> list[STFEvent]:
    now = Time.rn()

    events: list[STFEvent] = []

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(
            urljoin(BASE_URL, "api/v1/streams"),
            log=log,
        ):
            api_data: dict[str, list[dict[str, Any]]] = r.json()

            api_data["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    start_dt = now.delta(hours=-3)

    for stream_info in api_data.get("streams", []):
        if not all(
            values := [
                stream_info.get(x)
                for x in (
                    "league",
                    "category",
                    "name",
                    "match_timestamp",
                    "stream_key",
                )
            ]
        ):
            continue

        sport, category, name, event_time, stream_key = values

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        event_dt = Time.from_ts(event_time)

        if not start_dt <= event_dt.delta(minutes=30) <= now:
            continue

        events.append(
            STFEvent(
                sport=sport,
                name=name,
                category=category,
                stream_key=quote(stream_key),
                logo=stream_info.get("thumbnail_url"),
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
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                stream_key=ev.stream_key,
                category=ev.category,
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
                "logo": ev.logo or logo,
                "refer": urljoin(BASE_URL, f"embed/{ev.category}/{ev.stream_key}"),
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
            }

            cached_urls[key] = entry

            if source:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
