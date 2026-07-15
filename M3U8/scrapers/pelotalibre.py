import re
from collections import defaultdict
from functools import partial
from urllib.parse import parse_qsl, urljoin, urlsplit

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PLIBRE"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://la20hd.com"


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


async def get_events() -> list[Event]:
    events: list[Event] = []

    if not (
        api_req := await network.request(
            urljoin(BASE_URL, "eventos/json/agenda123.json"),
            log=log,
        )
    ):
        return events

    elif not (api_data := api_req.json()):
        return events

    counter = defaultdict(int)

    for event in api_data:
        if not all(
            values := [
                event.get(x)
                for x in (
                    "title",
                    "language",
                    "link",
                )
            ]
        ):
            continue

        title, lang, link = values

        if not (splits := urlsplit(link)).query:
            continue

        elif not dict(parse_qsl(splits.query)).get("stream"):
            continue

        sport, event_name = (s.strip() for s in title.split(":", 1))

        counter[x := f"{event_name} | {lang.upper()}"] += 1

        events.append(
            Event(
                sport=sport,
                name=f"{x} {counter[x]}",
                link=link,
            )
        )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["source"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info('Scraping from "https://la18hd.com"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            source = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "source": source,
                "logo": logo,
                "refer": ev.link,
                "timestamp": now.timestamp(),
                "tvg-id": tvg_id or "Live.Event.us",
            }

            cached_urls[key] = entry

            if source:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
