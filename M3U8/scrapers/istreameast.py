import base64
import re

import httpx
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "ISTRMEST"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=3_600)

BASE_URL = "https://istreameast.app"


async def get_html_data(client: httpx.AsyncClient, url: str) -> str:
    try:
        r = await client.get(url)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')

        return b""

    return r.text


async def process_event(
    client: httpx.AsyncClient,
    url: str,
    url_num: int,
) -> str | None:

    pattern = re.compile(r"source:\s*window\.atob\(\s*'([^']+)'\s*\)", re.IGNORECASE)

    if not (event_data := await get_html_data(client, url)):
        log.warning(f"URL {url_num}) Failed to load event url.")
        return

    soup = HTMLParser(event_data)

    if not (iframe := soup.css_first("iframe#wp_player")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    if not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return

    if not (iframe_src_data := await get_html_data(client, iframe_src)):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return

    if not (match := pattern.search(iframe_src_data)):
        log.warning(f"URL {url_num}) No Clappr source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")
    return base64.b64decode(match[1]).decode("utf-8")


async def get_events(
    client: httpx.AsyncClient, cached_keys: set[str]
) -> list[dict[str, str]]:
    pattern = re.compile(r"^(?:LIVE|\d+\s+(minutes?)\b)", re.IGNORECASE)

    html_data = await get_html_data(client, BASE_URL)

    soup = HTMLParser(html_data)

    events = []

    for link in soup.css("li.f1-podium--item > a.f1-podium--link"):
        li_item = link.parent

        if not (rank_elem := li_item.css_first(".f1-podium--rank")):
            continue

        sport = rank_elem.text(strip=True)

        if not (driver_elem := li_item.css_first(".f1-podium--driver")):
            continue

        event_name = driver_elem.text(strip=True)

        if inner_span := driver_elem.css_first("span.d-md-inline"):
            event_name = inner_span.text(strip=True)

        if not (href := link.attributes.get("href")):
            continue

        if not (time_elem := li_item.css_first(".SaatZamanBilgisi")):
            continue

        time_text = time_elem.text(strip=True)

        if not pattern.search(time_text):
            continue

        key = f"[{sport}] {event_name} ({TAG})"

        if cached_keys & {key}:
            continue

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": href,
            }
        )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    cached_count = len(cached_urls)
    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(client, set(cached_urls.keys()))

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now()).timestamp()

        for i, ev in enumerate(events, start=1):
            if url := await process_event(
                client,
                ev["link"],
                i,
            ):
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
                    "base": "https://gooz.aapmains.net",
                    "timestamp": now,
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                urls[key] = cached_urls[key] = entry

    if new_count := len(cached_urls) - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
