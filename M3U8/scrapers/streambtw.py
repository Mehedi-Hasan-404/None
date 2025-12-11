import base64
import re
from functools import partial
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

CACHE_FILE = Cache("streambtw.json", exp=3_600)

BASE_URL = "https://streambtw.com"

TAG = "STRMBTW"


def fix_league(s: str) -> str:
    pattern = re.compile(r"^\w*-\w*", re.IGNORECASE)

    return " ".join(s.split("-")) if pattern.search(s) else s


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

    valid_m3u8 = re.compile(r'var\s+(\w+)\s*=\s*"([^"]*)"', re.IGNORECASE)

    if not (match := valid_m3u8.search(r.text)):
        log.info(f"URL {url_num}) No M3U8 found")
        return

    encoded = match[2][::-1]
    decoded = base64.b64decode(encoded[::-1]).decode("utf-8")
    log.info(f"URL {url_num}) Captured M3U8")
    return decoded


async def get_events(client: httpx.AsyncClient) -> list[dict[str, str]]:
    try:
        r = await client.get(BASE_URL)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{BASE_URL}": {e}')

        return []

    soup = HTMLParser(r.content)

    events = []

    for card in soup.css("div.container div.card"):
        link = card.css_first("a.btn.btn-primary")

        if not (href := link.attrs.get("href")):
            continue

        league = card.css_first("h5.card-title").text(strip=True)

        name = card.css_first("p.card-text").text(strip=True)

        events.append(
            {
                "sport": fix_league(league),
                "event": name,
                "link": urljoin(BASE_URL, href),
            }
        )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(client)

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
                    "base": link,
                    "timestamp": now,
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                urls[key] = entry

    log.info(f"Collected {len(urls)} event(s)")

    CACHE_FILE.write(urls)
