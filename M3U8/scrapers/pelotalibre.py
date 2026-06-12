import re
from functools import partial

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PLIBRE"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://la18hd.com"

API_URL = f"{BASE_URL}/eventos/json/agenda123.json"


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    valid_m3u8 = re.compile(r'var\s+playbackURL\s+=\s+"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(html_data.text)):
        log.warning(f"URL {url_num}) No M3U8 found")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match[1]


async def get_events() -> list[dict[str, str]]:
    events = []

    if not (api_req := await network.request(API_URL, log=log)):
        return events

    elif not (api_data := api_req.json()):
        return events

    for event in api_data:
        if not (name := event.get("title")) or not (link := event.get("link")):
            continue

        elif not link.startswith(BASE_URL) or "drm.php" in link:
            continue

        if (sport := event.get("category")) and sport == "Other":
            sport = "Live Event"

        if all(existing_event["event"] != name for existing_event in events):
            events.append(
                {
                    "sport": sport,
                    "event": name,
                    "link": link,
                }
            )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["url"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info('Scraping from "https://la18hd.com"')

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
