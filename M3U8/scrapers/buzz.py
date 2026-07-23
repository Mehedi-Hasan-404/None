from collections.abc import KeysView
from dataclasses import dataclass
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "BUZZ"

CACHE_FILE = Cache(TAG, exp=5_400)

HTML_FILE = Cache(f"{TAG}-html", exp=28_800)

BASE_URL = "https://streamed.buzz"


@dataclass(kw_only=True, slots=True)
class BZEvent(Event):
    event_ts: int | float


async def refresh_html_cache(now: Time) -> dict[str, dict[str, str | float]]:
    events = {}

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for game in soup.css("tr.event-group"):
        if not all(
            values := [
                game.css_first(x)
                for x in (
                    "td.category-name",
                    "td.team-name",
                    "td",
                    "a.watch-btn",
                )
            ]
        ):
            continue

        sport, event_name, event_date, ch_id = (x.text(strip=True) for x in values)

        event_dt = Time.from_str(event_date.replace("\t", " "), timezone="EST")

        key = f"[{sport}] {event_name} ({TAG})"

        events[key] = {
            "sport": sport,
            "name": event_name,
            "link": urljoin(str(html_data.url), f"set.php?{ch_id}"),
            "event_ts": event_dt.timestamp(),
            "timestamp": now.timestamp(),
        }

    return events


async def get_events(cached_keys: KeysView[str]) -> list[BZEvent]:
    now = Time.clean(Time.now())

    if not (events := HTML_FILE.load()):
        log.info("Refreshing HTML cache")

        events = await refresh_html_cache(now)

        HTML_FILE.write(events)

    start_ts = now.delta(hours=-3).timestamp()
    end_ts = now.delta(minutes=30).timestamp()

    return [
        BZEvent(**v)
        for k, v in events.items()
        if k not in cached_keys and start_ts <= v["event_ts"] <= end_ts
    ]


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["source"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=ev.link,
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    source = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

                    key = f"[{ev.sport}] {ev.name} ({TAG})"

                    entry = {
                        "source": source,
                        "logo": logo,
                        "refer": "https://exposestrat.com",
                        "timestamp": ev.event_ts,
                        "tvg-id": tvg_id or "Live.Event.us",
                        "link": ev.link,
                    }

                    cached_urls[key] = entry

                    if source:
                        valid_count += 1

                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
