import re
from functools import partial
from urllib.parse import quote, urljoin

import httpx
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

CACHE_FILE = Cache("fawa.json", exp=10_800)

BASE_URL = "http://www.fawanews.sc/"

TAG = "FAWA"


async def process_event(
    client: httpx.AsyncClient,
    url: str,
    url_num: int,
) -> str | None:

    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        log.error(f'URL {url_num}) Failed to fetch "{url}": {e}')
        return

    valid_m3u8 = re.compile(
        r'var\s+(\w+)\s*=\s*\[["\']?(https?:\/\/[^"\'\s>]+\.m3u8(?:\?[^"\'\s>]*)?)["\']\]?',
        re.IGNORECASE,
    )

    if not (match := valid_m3u8.search(r.text)):
        log.info(f"URL {url_num}) No M3U8 found")
        return

    log.info(f"URL {url_num}) Captured M3U8")
    return match[2]


async def get_events(
    client: httpx.AsyncClient,
    cached_hrefs: set[str],
) -> list[dict[str, str]]:
    try:
        r = await client.get(BASE_URL)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{BASE_URL}": {e}')

        return []

    soup = HTMLParser(r.content)

    valid_event = re.compile(r"\d{1,2}:\d{1,2}")
    clean_event = re.compile(r"\s+-+\s+\w{1,4}")

    events = []

    for item in soup.css(".user-item"):
        text = item.css_first(".user-item__name")
        subtext = item.css_first(".user-item__playing")
        link = item.css_first("a[href]")

        if not (href := link.attributes.get("href")):
            continue

        href = quote(href)

        if cached_hrefs & {href}:
            continue

        if not (text and subtext):
            continue

        event_name, details = text.text(strip=True), subtext.text(strip=True)

        if not (valid_event.search(details)):
            continue

        sport = valid_event.split(details)[0].strip()

        events.append(
            {
                "sport": sport,
                "event": clean_event.sub("", event_name),
                "link": urljoin(BASE_URL, href),
                "href": href,
            }
        )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    cached_hrefs = {entry["href"] for entry in cached_urls.values()}
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(client, cached_hrefs)

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.now().timestamp()

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                client=client,
                url=ev["link"],
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                log=log,
                timeout=10,
            )

            if url:
                sport, event, link = (
                    ev["sport"],
                    ev["event"],
                    ev["link"],
                )
                key = f"[{sport}] {event} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": url,
                    "logo": logo,
                    "base": BASE_URL,
                    "timestamp": now,
                    "id": tvg_id or "Live.Event.us",
                    "href": ev["href"],
                    "link": link,
                }

                urls[key] = cached_urls[key] = entry

    if new_count := len(cached_urls) - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
