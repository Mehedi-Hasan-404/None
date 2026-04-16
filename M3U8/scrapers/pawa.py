import base64
import re
from functools import partial

import feedparser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PAWA"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://pawastreams.net/feed/"


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")

        return

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first("iframe")):
        log.warning(f"URL {url_num}) No iframe element found.")

        return

    if not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")

        return

    if not (iframe_src_data := await network.request(iframe_src, log=log)):
        log.warning(f"URL {url_num}) Failed to load iframe source.")

        return

    pattern = re.compile(r"source:\s*window\.atob\(\s*'([^']+)'\s*\)", re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")

        return

    log.info(f"URL {url_num}) Captured M3U8")

    m3u = base64.b64decode(match[1]).decode("utf-8")

    return m3u.split("&remote")[0]


async def get_events() -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    feed = feedparser.parse(html_data.content)

    sport = "Live Event"

    for entry in feed.entries:
        if not (link := entry.get("link")):
            continue

        if not (title := entry.get("title")):
            continue

        title = title.replace(" v ", " vs ")

        events.append(
            {
                "sport": sport,
                "event": title,
                "link": link,
            }
        )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update(cached_urls)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            sport, event = ev["sport"], ev["event"]

            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": url,
                "logo": logo,
                "base": link,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "link": link,
            }

            cached_urls[key] = entry

            if url:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
