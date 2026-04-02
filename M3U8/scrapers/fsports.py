import asyncio
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FSPRTS"

CACHE_FILE = Cache(TAG, exp=5_400)

BASE_URL = "https://fsportshds.xyz"

SPORT_URLS = {
    # "Fighting": urljoin(BASE_URL, "mmastreams.php"),
    "Basketball": urljoin(BASE_URL, "nbastreams.php"),
    # "Ice Hockey": urljoin(BASE_URL, "nhlstreams.php"),
    # "American Football": urljoin(BASE_URL, "nflstreams.php")
} | {
    sport: urljoin(BASE_URL, f"{sport}streams.php".lower())
    for sport in [
        "Football",
        # "Boxing",
        # "F1",
        # "MLB",
        # "MotoGP",
    ]
}


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

        for card in soup.css(".media.btn.btn-default.btn-lg.btn-block"):
            if not (name_elem := card.css_first("h4")):
                continue

            if card.css_first('[id^="countdown-"]'):
                continue

            if not (a_elem := card.css_first("a")) or not (
                href := a_elem.attributes.get("href")
            ):
                continue

            name = name_elem.text(strip=True)

            if f"[{sport}] {name} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": name,
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

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
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

                    sport, event = ev["sport"], ev["event"]

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://vividmosaica.com/",
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
