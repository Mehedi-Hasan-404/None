import json
import re
from dataclasses import dataclass
from functools import partial
from urllib.parse import parse_qsl, quote, urljoin, urlsplit

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "XYZ2"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://xyzstreams.st"


@dataclass(kw_only=True, slots=True)
class XYZEvent(Event):
    logo: str | None = None


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, url_num, log=log)):
        return

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first("iframe#stream-player")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    elif not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")
        return

    splits = urlsplit(iframe_src)

    if params := dict(parse_qsl(splits.query)):
        stream_id = params.get("streamid")
        pro_id = params.get("proid")

        if not (stream_id and pro_id):
            log.warning(f"URL {url_num}) Unable to parse iframe src queries.")
            return

        m3u = f"https://247v2.dlhd.net/?stream_id={quote(stream_id)}&pro_id={quote(pro_id)}&index.m3u8"

    else:
        q = urlsplit(iframe_src).query or "default-id"

        m3u = f"http://iptvstream.dlhd.net/{q}/mono.ts.m3u8"

    log.info(f"URL {url_num}) Captured M3U8")

    return m3u


async def get_events() -> list[XYZEvent]:
    events: list[XYZEvent] = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    now = Time.rn()

    ptrn = re.compile(r"const\s+EVENTS_DATA\s*=\s*(\[.*?\n\])", re.S)

    if not (match := ptrn.search(html_data.text)):
        return events

    raw = match[1]

    quoted = re.sub(r"([{,]\s*)([A-Za-z_$][A-Za-z0-9_$]*)\s*:", r'\1"\2":', raw)

    event_data: list[dict[str, str]] = json.loads(quoted)

    for game in event_data:
        if not all(
            values := [
                game.get(x)
                for x in (
                    "title",
                    "href",
                    "start",
                )
            ]
        ):
            continue

        name, href, event_start = values

        sport = game.get("category", "Live Event")

        event_dt = Time.fromisoformat(event_start).to_tz("EST")

        if event_dt.date() != now.date():
            continue

        events.append(
            XYZEvent(
                sport=sport,
                name=name,
                logo=game.get("bg"),
                link=urljoin(BASE_URL, href),
                timestamp=now.timestamp(),
            )
        )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["source"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} new URL(s)")

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
                "logo": ev.logo or logo,
                "refer": BASE_URL,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
                "link": ev.link,
            }

            cached_urls[key] = entry

            if source:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
