import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(TAG, exp=28_800)

API_URL = "https://backend.streamcenter.live/api/Parties"

BASE_URL = "https://streams.center"

CATEGORIES = {
    4: "Basketball",
    9: "FIFA World Cup",
    13: "MLB",
    # 14: "NFL",
    15: "Motor Sport",
    # 16: "NHL",
    17: "Fight MMA",
    18: "Boxing",
    20: "WWE",
    21: "Tennis",
}


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    if not (
        iframe_src_data := await network.request(
            network.ensure_https(src),
            headers={"Referer": url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return

    pattern = re.compile(r'input:\s+"([^"]*)"', re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No encrypted URL found.")
        return

    if not (
        decrypted := await network.client.post(
            urljoin(BASE_URL, "embed/decrypt.php"),
            data={"input": match[1]},
        )
    ):
        log.warning(f"URL {url_num}) Failed to decrypt URL.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return decrypted.text.split("?")[0]


async def get_events() -> list[Event]:
    events: list[Event] = []

    if not (
        r := await network.request(
            API_URL,
            params={"pageNumber": 1, "pageSize": 500},
            log=log,
        )
    ):
        return events

    now = Time.clean(Time.now())

    api_data: list[dict] = r.json()

    for stream_group in api_data:
        if not all(
            values := [
                stream_group.get(x)
                for x in (
                    "categoryId",
                    "gameName",
                    "videoUrl",
                    "beginPartie",
                )
            ]
        ):
            continue

        category_id, title, iframes, event_time = values

        if not (sport := CATEGORIES.get(category_id)):
            continue

        event_dt = Time.from_str(event_time, timezone="CET")

        if event_dt.date() != now.date():
            continue

        stream_urls: dict[str, str] = {
            url: lang
            for entry in iframes.split(";")[::-1]
            for url, lang in [entry.split("<")]
        }

        events.extend(
            Event(
                sport=sport,
                name=f"{title} | {lang}",
                link=url,
                timestamp=now.timestamp(),
            )
            for url, lang in stream_urls.items()
        )
    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["source"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

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
