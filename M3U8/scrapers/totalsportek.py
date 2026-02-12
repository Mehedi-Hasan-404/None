from functools import partial
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TOTALSPRTK"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://live3.totalsportek777.com"


def fix_txt(s: str) -> str:
    s = " ".join(s.split())

    return s.upper() if s.islower() else s


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    sport = "Live Event"

    for node in soup.css("a"):
        if not node.attributes.get("class"):
            continue

        if (parent := node.parent) and "my-1" in parent.attributes.get("class", ""):
            if span := node.css_first("span"):
                sport = span.text(strip=True)

        sport = fix_txt(sport)

        if not (teams := [t.text(strip=True) for t in node.css(".col-7 .col-12")]):
            continue

        if not (href := node.attributes.get("href")):
            continue

        href = urlparse(href).path if href.startswith("http") else href

        if not (time_node := node.css_first(".col-3 span")):
            continue

        if time_node.text(strip=True) != "MatchStarted":
            continue

        event_name = fix_txt(" vs ".join(teams))

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        events.append(
            {
                "sport": sport,
                "event": event_name,
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

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now())

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
                        timeout=6,
                    )

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

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1

                        urls[key] = entry

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
