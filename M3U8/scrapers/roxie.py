import asyncio
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "ROXIE"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

BASE_URL = "https://roxiestreams.info"

SPORT_ENDPOINTS = {
    "fighting": "Fighting",
    # "mlb": "MLB",
    "motorsports": "Racing",
    "nba": "NBA",
    # "nfl": "American Football",
    "nhl": "NHL",
    "soccer": "Soccer",
}


async def refresh_html_cache(
    url: str,
    sport: str,
    now_ts: float,
) -> dict[str, dict[str, str | float]]:

    events = {}

    if not (html_data := await network.request(url, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for row in soup.css("table#eventsTable tbody tr"):
        if not (a_tag := row.css_first("td a")):
            continue

        event = a_tag.text(strip=True)

        if not (href := a_tag.attributes.get("href")):
            continue

        if not (span := row.css_first("span.countdown-timer")):
            continue

        data_start = span.attributes["data-start"].rsplit(":", 1)[0]

        event_dt = Time.from_str(data_start, timezone="PST")

        event_sport = SPORT_ENDPOINTS[sport]

        key = f"[{event_sport}] {event} ({TAG})"

        events[key] = {
            "sport": event_sport,
            "event": event,
            "link": href,
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }

    return events


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> str | None:

    captured: list[str] = []

    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=15_000,
        )

        try:
            if btn := await page.wait_for_selector(
                "button:has-text('Stream 1')",
                timeout=5_000,
            ):
                await btn.click()
        except TimeoutError:
            pass

        wait_task = asyncio.create_task(got_one.wait())

        try:
            await asyncio.wait_for(wait_task, timeout=6)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")
            return

        finally:
            if not wait_task.done():
                wait_task.cancel()

                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")
            return captured[0]

        log.warning(f"URL {url_num}) No M3U8 captured after waiting.")
        return

    except Exception as e:
        log.warning(f"URL {url_num}) Exception while processing: {e}")
        return

    finally:
        page.remove_listener("request", handler)


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")

        sport_urls = {sport: urljoin(BASE_URL, sport) for sport in SPORT_ENDPOINTS}

        tasks = [
            refresh_html_cache(
                url,
                sport,
                now.timestamp(),
            )
            for sport, url in sport_urls.items()
        ]

        results = await asyncio.gather(*tasks)

        events = {k: v for data in results for k, v in data.items()}

        HTML_CACHE.write(events)

    live = []

    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=5).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
            continue

        live.append(v)

    return live


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=ev["link"],
                        url_num=i,
                        page=page,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event, ts, link = (
                        ev["sport"],
                        ev["event"],
                        ev["event_ts"],
                        ev["link"],
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
                        valid_count += 1

                        urls[key] = entry

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
