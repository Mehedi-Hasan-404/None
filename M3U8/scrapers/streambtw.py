import base64
import json
import re
from functools import partial

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMBTW"

CACHE_FILE = Cache(TAG, exp=3_600)

BASE_URL = "https://hiteasport.info"


def fix_league(s: str) -> str:
    pattern = re.compile(r"^\w*-\w*", re.IGNORECASE)

    return " ".join(s.split("-")) if pattern.search(s) else s


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        return

    valid_m3u8 = re.compile(r'var\s+(\w+)\s*=\s*"([^"]*)"', re.IGNORECASE)

    if not (match := valid_m3u8.search(html_data.text)):
        log.info(f"URL {url_num}) No M3U8 found")

        return

    stream_link: str = match[2]

    if not stream_link.startswith("http"):
        stream_link = base64.b64decode(stream_link).decode("utf-8")

    log.info(f"URL {url_num}) Captured M3U8")

    return stream_link


async def get_events() -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    script_text = None

    for s in soup.css("script"):
        t = s.text() or ""

        if "const DATA" in t:
            script_text = t
            break

    if not script_text:
        return events

    if not (
        match := re.search(r"const\s+DATA\s*=\s*(\[\s*.*?\s*\]);", script_text, re.S)
    ):
        return events

    data_js = match[1].replace("\n      ", "").replace("\n    ", "")
    s1 = re.sub(r"{\s", '{"', data_js)
    s2 = re.sub(r':"', '":"', s1)
    s3 = re.sub(r":\[", '":[', s2)
    s4 = re.sub(r"},\]", "}]", s3)
    s5 = re.sub(r'",\s', '","', s4)

    data: list[dict[str, str]] = json.loads(s5)

    for matches in data:
        league = matches["title"]

        items: list[dict[str, str]] = matches["items"]

        for info in items:
            title = info["title"]

            url = info["url"]

            events.append(
                {
                    "sport": fix_league(league),
                    "event": title,
                    "link": url,
                }
            )

    return events


async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events()

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev["link"],
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
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
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                urls[key] = entry

    log.info(f"Collected {len(urls)} event(s)")

    CACHE_FILE.write(urls)
