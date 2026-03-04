from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TVAPP"

CACHE_FILE = Cache(TAG, exp=86_400)

BASE_URL = "https://thetvapp.to"


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")

        return

    soup = HTMLParser(html_data.content)

    if not (channel_name_elem := soup.css_first("#stream_name")):
        log.warning(f"URL {url_num}) No channel name elem found.")

        return

    if not (channel_name := channel_name_elem.attributes.get("name")):
        log.warning(f"URL {url_num}) No channel name found.")

        return

    log.info(f"URL {url_num}) Captured M3U8")

    return f"http://origin.thetvapp.to/hls/{channel_name.strip()}/mono.m3u8"


async def get_events() -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for row in soup.css(".row"):
        if not (h3_elem := row.css_first("h3")):
            continue

        if (sport := h3_elem.text(strip=True)).lower() == "live tv channels":
            continue

        for a in row.css("a.list-group-item[href]"):
            splits = a.text(strip=True).split(":")

            del splits[-3:]

            event_name = ":".join(splits)

            if not (href := a.attributes.get("href")):
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
                    "link": urljoin(BASE_URL, href),
                }
            )

    return events


async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
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

            if url:
                sport, event = ev["sport"], ev["event"]

                key = f"[{sport}] {event} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": url,
                    "logo": logo,
                    "base": BASE_URL,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                urls[key] = entry

    log.info(f"Collected and cached {len(urls)} new event(s)")

    CACHE_FILE.write(urls)
