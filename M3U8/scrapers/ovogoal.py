import re
from functools import partial

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "OVOGOAL"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

BASE_URL = "https://ovogoal.plus"


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (html_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")
        return nones

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return nones

    if not (iframe_src_data := await network.request(iframe_src, log=log)):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return nones

    valid_m3u8 = re.compile(r'(var|const)\s+(\w+)\s*=\s*"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return match[3], iframe_src


async def refresh_html_cache() -> dict[str, dict[str, str | float]]:
    log.info("Refreshing HTML cache")

    now = Time.clean(Time.now())

    events = {}

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for card in soup.css(".stream-row"):
        if (not (watch_btn_elem := card.css_first(".watch-btn"))) or (
            not (onclick := watch_btn_elem.attributes.get("onclick"))
        ):
            continue

        if not (event_time_elem := card.css_first(".stream-time")):
            continue

        if not (event_name_elem := card.css_first(".stream-info")):
            continue

        href = onclick.split(".href=")[-1].replace("'", "")

        event_name = event_name_elem.text(strip=True)

        event_time = event_time_elem.text(strip=True)

        event_dt = Time.from_str(f"{now.date()} {event_time}", timezone="CET")

        sport = "Live Event"

        key = f"[{sport}] {event_name} ({TAG})"

        events[key] = {
            "sport": sport,
            "event": event_name,
            "link": href,
            "event_ts": event_dt.timestamp(),
            "timestamp": now.timestamp(),
        }

    return events


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        events = await refresh_html_cache()

        HTML_CACHE.write(events)

    live = []

    start_ts = now.delta(minutes=-30).timestamp()
    end_ts = now.delta(minutes=30).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
            continue

        live.append(v)

    return live


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev["link"],
                url_num=i,
            )

            url, iframe = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            sport, event, link, ts = (
                ev["sport"],
                ev["event"],
                ev["link"],
                ev["event_ts"],
            )

            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": url,
                "logo": logo,
                "base": iframe,
                "timestamp": ts,
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
