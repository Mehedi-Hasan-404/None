import json
import re
from collections import defaultdict
from functools import partial
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STP"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://streamtp.sbs"


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    valid_m3u8 = re.compile(r'var\s+playbackURL\s+=\s+"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(html_data.text)):
        log.warning(f"URL {url_num}) No M3U8 found")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    m3u8: str = json.loads(f'"{match[1]}"')

    splits = urlsplit(m3u8)

    params = [(k, v) for k, v in parse_qsl(splits.query) if k.lower() != "ip"]

    return urlunsplit(splits._replace(query=urlencode(params)))


async def get_events() -> list[Event]:
    events: list[Event] = []

    if not (api_req := await network.request(urljoin(BASE_URL, "wc.json"), log=log)):
        return events

    elif not (api_data := api_req.json()):
        return events

    counter = defaultdict(int)

    for event in api_data.get("events", []):
        title = event["title"]

        if (sport := event["category"]) == "Other":
            sport = "Live Event"

        if not (links := event.get("links")):
            continue

        stream_urls: dict[str, str] = {
            link["url"]: link["lang"]["label"] for link in links
        }

        for url, lang in stream_urls.items():
            if not url.startswith(BASE_URL):
                continue

            counter[name := f"{title.split("|")[0].strip()} | {lang.upper()}"] += 1

            events.append(
                Event(
                    sport=sport,
                    name=f"{name} {counter[name]}",
                    link=url,
                )
            )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["m3u8"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info('Scraping from "https://streamtpnew.com"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            m3u8 = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "m3u8": m3u8,
                "logo": logo,
                "refer": ev.link,
                "timestamp": now.timestamp(),
                "tvg-id": tvg_id or "Live.Event.us",
            }

            cached_urls[key] = entry

            if m3u8:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
