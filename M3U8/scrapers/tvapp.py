from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TVAPP"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

BASE_URL = "https://thetvapp.to"


async def refresh_html_cache(now_ts: float) -> dict[str, dict[str, str | float]]:
    log.info("Refreshing HTML cache")

    events = {}

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for row in soup.css(".row"):
        if not (h3_elem := row.css_first("h3")):
            continue

        sport = h3_elem.text(strip=True)

        if sport.lower() == "live tv channels":
            continue

        for a in row.css("a.list-group-item[href]"):
            if not (href := a.attributes.get("href")):
                continue

            if not (span := a.css_first("span")):
                continue

            event_time = span.text(strip=True)

            event_dt = Time.from_str(event_time, timezone="UTC")

            event_name = a.text(strip=True).split(":")[0]

            key = f"[{sport}] {event_name} ({TAG})"

            events[key] = {
                "sport": sport,
                "event": event_name,
                "link": urljoin(BASE_URL, href),
                "event_ts": event_dt.timestamp(),
                "timestamp": now_ts,
            }

    return events


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        events = await refresh_html_cache(now.timestamp())

        HTML_CACHE.write(events)

    live = []

    start_ts = now.delta(minutes=-30).timestamp()
    end_ts = now.delta(minutes=30).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
            continue

        live.append({**v})

    return live


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=ev["link"],
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    if url:
                        sport, event, ts, link = (
                            ev["sport"],
                            ev["event"],
                            ev["event_ts"],
                            ev["link"],
                        )

                        key = f"[{sport}] {event} ({TAG})"

                        tvg_id, logo = leagues.get_tvg_info(sport, event)

                        entry = {
                            "url": url,
                            "logo": logo,
                            "base": BASE_URL,
                            "timestamp": ts,
                            "id": tvg_id or "Live.Event.us",
                            "link": link,
                        }

                        urls[key] = cached_urls[key] = entry

    if new_count := len(cached_urls) - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
