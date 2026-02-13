from functools import partial
from urllib.parse import urljoin, urlparse

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TVAPP"

CACHE_FILE = Cache(TAG, exp=86_400)

BASE_URL = "https://thetvapp.to"


def fix_url(s: str) -> str:
    parsed = urlparse(s)

    base = f"origin.{parsed.netloc.split('.', 1)[-1]}"

    return urljoin(f"http://{base}", parsed.path.replace("tracks-v1a1/", ""))


async def get_events() -> list[dict[str, str]]:
    events = []

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
            event_name = a.text(strip=True).split(":", 1)[0]

            if not (href := a.attributes.get("href")):
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
    if cached := CACHE_FILE.load():
        urls.update(cached)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events()

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
                    )

                    if url:
                        sport, event, link = (
                            ev["sport"],
                            ev["event"],
                            ev["link"],
                        )

                        key = f"[{sport}] {event} ({TAG})"

                        tvg_id, logo = leagues.get_tvg_info(sport, event)

                        entry = {
                            "url": fix_url(url),
                            "logo": logo,
                            "base": BASE_URL,
                            "timestamp": now.timestamp(),
                            "id": tvg_id or "Live.Event.us",
                            "link": link,
                        }

                        urls[key] = entry

    log.info(f"Collected and cached {len(urls)} new event(s)")

    CACHE_FILE.write(urls)
