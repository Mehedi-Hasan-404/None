#!/usr/bin/env python3

import re
from functools import partial
from urllib.parse import urljoin

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "S2WATCH"

CACHE_FILE = Cache(TAG, exp=10_800)

API_CACHE = Cache(f"{TAG}-api", exp=28_800)

API_URL = "https://s2watch.me/api/v1/schedule/list"

BASE_URL = "https://zarviro.link"


async def process_event(event_id: int, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (
        base_api_src := await network.request(
            urljoin(BASE_URL, "api/player.php"),
            params={"id": event_id},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to get iframe url.")
        return nones

    if not (embed_url := base_api_src.json().get("url")):
        log.warning(f"URL {url_num}) No iframe url available.")
        return nones

    if not (
        event_data := await network.request(
            embed_url,
            log=log,
            headers={
                "User-Agent": "curl/8.19.0",
                "Referer": f"{base_api_src.url}",
            },
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe url.")
        return nones

    pattern = re.compile(r'var\s+src\s+=\s+"([^"]*)"', re.I)

    if not (match := pattern.search(event_data.text)):
        log.warning(f"URL {url_num}) No source found.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return match[1], embed_url


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_CACHE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(API_URL, log=log):
            api_data: list[dict] = r.json().get("matches", [])

            api_data[-1]["timestamp"] = now.timestamp()

        API_CACHE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    for info in api_data:
        team_1, team_2 = info["team1"], info["team2"]

        sport = info["league"]

        event_name = f"{team_1} vs {team_2}"

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        channels: list[dict] = info["channels"]

        if (not channels) or (not (event_id := channels[0].get("number"))):
            continue

        event_ts = int(f'{info["startTimestamp"]}'[:-3])

        event_dt = Time.from_ts(event_ts)

        if not start_dt <= event_dt <= end_dt:
            continue

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "event_id": event_id,
                "timestamp": event_ts,
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://s2watch.me"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                event_id=(event_id := ev["event_id"]),
                url_num=i,
            )

            url, iframe = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
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
                "base": iframe,
                "timestamp": ts,
                "id": tvg_id or "Live.Event.us",
                "link": urljoin(BASE_URL, f"ch?id={event_id}"),
                "UA": "curl/8.19.0",
            }

            cached_urls[key] = entry

            if url:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
