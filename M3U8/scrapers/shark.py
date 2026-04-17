import re
from functools import partial

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "SHARK"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://sharkstreams.net"


async def process_event(url: str, url_num: int) -> str | None:
    if not (r := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")

        return

    data: dict[str, list[str]] = r.json()

    if not (urls := data.get("urls")):
        log.warning(f"URL {url_num}) No M3U8 found")

        return

    pattern = re.compile(r"playlist\.m3u8\?.*$", re.I)

    log.info(f"URL {url_num}) Captured M3U8")

    return pattern.sub(r"chunks.m3u8", urls[0])


async def get_events() -> dict[str, dict[str, str | float]]:
    now = Time.clean(Time.now())

    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    pattern = re.compile(r"openEmbed\('([^']+)'\)", re.I)

    soup = HTMLParser(html_data.content)

    for row in soup.css(".row"):
        date_node = row.css_first(".ch-date")

        sport_node = row.css_first(".ch-category")
        name_node = row.css_first(".ch-name")

        if not (date_node and sport_node and name_node):
            continue

        event_dt = Time.from_str(date_node.text(strip=True), timezone="EST")

        if event_dt.date() != now.date():
            continue

        sport = sport_node.text(strip=True)

        event_name = name_node.text(strip=True)

        embed_btn = row.css_first("a.hd-link.secondary")

        if not embed_btn or not (onclick := embed_btn.attributes.get("onclick")):
            continue

        if not (match := pattern.search(onclick)):
            continue

        link = match[1].replace("player.php", "get-stream.php")

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": link,
                "timestamp": now.timestamp(),
            }
        )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["url"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

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

            sport, event, ts = (
                ev["sport"],
                ev["event"],
                ev["timestamp"],
            )

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            key = f"[{sport}] {event} ({TAG})"

            entry = {
                "url": url,
                "logo": logo,
                "base": BASE_URL,
                "timestamp": ts,
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
