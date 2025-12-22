import re
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import async_playwright
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIME4TV"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=5_400)

BASE_URL = "https://time4tv.icu/"


def fix_league(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split()) if len(s) > 5 else s.upper()


async def get_events(cached_keys: list[str], now: Time) -> dict[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    pattern = re.compile(r"openPlayerPopup\(\s*(\d+)\s*\)", re.IGNORECASE)

    soup = HTMLParser(html_data.content)

    for row in soup.css(".wrap .row"):
        if not (date := row.css_first(".date")):
            continue

        event_date = date.text(strip=True).replace("\t", " ")

        try:
            event_dt = Time.from_str(event_date, fmt="%m/%d/%Y %I:%M %p")
        except ValueError:
            continue

        if event_dt.date() != now.date():
            continue

        league = row.css_first(".league")

        title = row.css_first(".title")

        hds_a = row.css_first(".hds a")

        if not (league and title and hds_a):
            continue

        sport, event = fix_league(league.text(strip=True)), title.text(strip=True)

        if f"[{sport}] {event} ({TAG})" in cached_keys:
            continue

        onclick = hds_a.attributes.get("onclick", "")

        if not (match := pattern.search(onclick)):
            continue

        events.append(
            {
                "sport": sport,
                "event": event,
                "link": urljoin(BASE_URL, f"player1.php?{match[1]}"),
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    now = Time.clean(Time.now())

    events = await get_events(cached_urls.keys(), now)

    log.info(f"Processing {len(events)} new URL(s)")

    async with async_playwright() as p:
        browser, context = await network.browser(p)

        for i, ev in enumerate(events, start=1):
            handler = partial(
                network.process_event,
                url=ev["link"],
                url_num=i,
                context=context,
                log=log,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                log=log,
            )

            if url:
                sport, event = ev["sport"], ev["event"]

                tvg_id, logo = leagues.info(sport)

                key = f"[{sport}] {event} ({TAG})"

                entry = {
                    "url": url,
                    "logo": logo,
                    "base": "https://vividmosaica.com/",
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                }

                urls[key] = cached_urls[key] = entry

        await browser.close()

    if new_count := len(cached_urls) - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
