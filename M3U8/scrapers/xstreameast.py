import asyncio
import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "XSTRMEST"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = "https://xstreameast.com"

SPORT_ENDPOINTS = [
    # "mlb",
    "mma",
    "nba",
    # "nfl",
    # "nhl",
    "soccer",
    "wwe",
]


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return nones

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return nones

    elif iframe_src == "about:blank":
        log.warning(f"URL {url_num}) No iframe element found.")
        return nones

    if not (iframe_src_data := await network.request(iframe_src, log=log)):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return nones

    valid_m3u8 = re.compile(r'(var|const)\s+(\w+)\s*=\s*"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")
        return nones

    if len(encoded := match[2]) < 20:
        encoded = match[3]

    log.info(f"URL {url_num}) Captured M3U8")

    return bytes.fromhex(encoded).decode("utf-8"), iframe_src


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    tasks = [
        network.request(
            urljoin(BASE_URL, f"categories/{sport}/"),
            log=log,
        )
        for sport in SPORT_ENDPOINTS
    ]

    results = await asyncio.gather(*tasks)

    events = []

    if not (soups := [HTMLParser(html.content) for html in results if html]):
        return events

    sport = "Live Event"

    for soup in soups:
        if sport_header := soup.css_first("h1.text-3xl"):
            header = sport_header.text(strip=True)

            sport = header.split("Streams")[0].strip()

        for card in soup.css("article.game-card"):
            if not (team_elem := card.css_first("h2.text-xl.font-semibold")):
                continue

            if not (link_elem := card.css_first("a.stream-button")) or not (
                href := link_elem.attributes.get("href")
            ):
                continue

            if (
                not (live_badge := card.css_first("span.bg-green-600"))
                or live_badge.text(strip=True) != "LIVE"
            ):
                continue

            event_name = team_elem.text(strip=True)

            if f"[{sport}] {event_name} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
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

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            url, iframe = await network.safe_process(
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
                "base": iframe,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "link": link,
            }

            cached_urls[key] = entry

            if url:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
