import asyncio
import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "VOLOKIT"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "http://volokit.xyz"

SPORT_ENDPOINTS = {
    "mlb": "MLB",
    # "nfl": "NFL",
    "nhl": "NHL",
}


def fix_event(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split())


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")

        return

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first('iframe[height="100%"]')):
        log.warning(f"URL {url_num}) No iframe element found.")

        return

    if not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")

        return

    if not (
        iframe_src_data := await network.request(
            iframe_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.info(f"URL {url_num}) Failed to load iframe source.")

        return

    pattern = re.compile(r'source:\s+"([^"]*)"', re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")

        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match[1]


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    sport_urls = {
        sport.upper(): urljoin(BASE_URL, f"sport/{sport}/") for sport in SPORT_ENDPOINTS
    }

    tasks = [network.request(url, log=log) for url in sport_urls.values()]

    results = await asyncio.gather(*tasks)

    events = []

    if not (
        soups := [(HTMLParser(html.content), html.url) for html in results if html]
    ):
        return events

    for soup, url in soups:
        for card in soup.css("#events .table .vevent.theevent"):
            if not (href := card.css_first("a").attributes.get("href")):
                continue

            if not (name_node := card.css_first(".teamtd.event")):
                continue

            name = fix_event(name_node.text(strip=True))

            sport = next((k for k, v in sport_urls.items() if v == url), "Live Event")

            if f"[{sport}] {name} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": name,
                    "link": href,
                }
            )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

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
                valid_count += 1

                urls[key] = entry

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
