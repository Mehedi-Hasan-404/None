from functools import partial

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(TAG, exp=28_800)

API_URL = "https://backend.streamcenter.live/api/Parties"

CATEGORIES = {
    4: "Basketball",
    9: "Football",
    13: "Baseball",
    # 14: "American Football",
    15: "Motor Sport",
    16: "Hockey",
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

    if not iframe or not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return f"https://mainstreams.pro/hls/{iframe_src.rsplit("=", 1)[-1]}.m3u8"


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    events = []

    if not (
        r := await network.request(
            API_URL,
            log=log,
            params={"pageNumber": 1, "pageSize": 500},
        )
    ):
        return events

    api_data: list[dict] = r.json()

    for stream_group in api_data:
        category_id: int = stream_group.get("categoryId")

        name: str = stream_group.get("gameName")

        iframe: str = stream_group.get("videoUrl")

        event_time: str = stream_group.get("beginPartie")

        if not (name and category_id and iframe and event_time):
            continue

        event_dt = Time.from_str(event_time, timezone="CET")

        if event_dt.date() != now.date():
            continue

        if not (sport := CATEGORIES.get(category_id)):
            continue

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": iframe.split("<")[0],
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://streamcenter.xyz"')

    if events := await get_events(cached_urls.keys()):
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
                semaphore=network.PW_S,
                log=log,
            )

            sport, event = ev["sport"], ev["event"]

            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": url,
                "logo": logo,
                "base": "https://streamcenter.xyz",
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
