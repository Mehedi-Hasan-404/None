import asyncio
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "ROXIE"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://roxiestreams.su"

SPORT_URLS = {
    # "March Madness": urljoin(BASE_URL, "march-madness"),
    "Racing": urljoin(BASE_URL, "motorsports"),
    # "American Football": urljoin(BASE_URL, "nfl"),
} | {
    sport: urljoin(BASE_URL, sport.lower())
    for sport in [
        "Fighting",
        "MLB",
        "NBA",
        "NHL",
        "Soccer",
    ]
}


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> str | None:

    try:
        resp = await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=6_000,
        )

        if not resp or resp.status != 200:
            log.warning(
                f"URL {url_num}) Status Code: {resp.status if resp else 'None'}"
            )
            return

        try:
            if btn := page.locator("button.streambutton").first:
                await btn.click(
                    force=True,
                    click_count=2,
                    timeout=3_000,
                )

            await page.wait_for_function(
                "() => typeof clapprPlayer !== 'undefined'",
                timeout=6_000,
            )

            stream = await page.evaluate("() => clapprPlayer.options.source")
        except TimeoutError:
            log.warning(f"URL {url_num}) Could not find Clappr source")
            return

        log.info(f"URL {url_num}) Captured M3U8")
        return stream

    except Exception as e:
        log.warning(f"URL {url_num}) {e}")
        return


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    tasks = [network.request(url, log=log) for url in SPORT_URLS.values()]

    results = await asyncio.gather(*tasks)

    events = []

    if not (
        soups := [(HTMLParser(html.content), html.url) for html in results if html]
    ):
        return events

    for soup, url in soups:
        sport = next((k for k, v in SPORT_URLS.items() if v == url), "Live Event")

        for row in soup.css("table#eventsTable tbody tr"):
            if not (a_tag := row.css_first("td a")):
                continue

            event = a_tag.text(strip=True)

            if not (href := a_tag.attributes.get("href")):
                continue

            if f"[{sport}] {event} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event,
                    "link": urljoin(BASE_URL, href),
                }
            )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    sport, event = ev["sport"], ev["event"]

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    key = f"[{sport}] {event} ({TAG})"

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": BASE_URL,
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
