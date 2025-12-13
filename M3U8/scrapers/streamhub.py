from functools import partial

import httpx
from playwright.async_api import async_playwright
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMHUB"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=10_800)

BASE_URL = "https://streamhub.pro/live-now"


async def get_events(
    client: httpx.AsyncClient, cached_keys: set[str]
) -> list[dict[str, str]]:
    try:
        r = await client.get(BASE_URL)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{BASE_URL}": {e}')

        return []

    soup = HTMLParser(r.content)

    events = []

    for event in soup.css(".events-section"):
        if not (title_node := event.css_first(".section-titlte")):
            continue

        sport = title_node.text(strip=True)

        if not event.css_first(".event-competitors"):
            continue

        home_team = event.css_first(".event-home-team").text(strip=True)
        away_team = event.css_first(".event-visitor-team").text(strip=True)

        logo = event.css_first(".league-icon img").attributes.get("src")

        if not (event_button := event.css_first("div.event-button a")) or not (
            href := event_button.attributes.get("href")
        ):
            continue

        event_name = f"{away_team} vs {home_team}"

        key = f"[{sport}] {event_name} ({TAG})"

        if cached_keys & {key}:
            continue

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": href,
                "logo": logo,
            }
        )

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    cached_urls = CACHE_FILE.load()
    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}
    valid_count = cached_count = len(valid_urls)
    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(client, set(cached_urls.keys()))

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.now().timestamp()

        async with async_playwright() as p:
            browser, context = await network.browser(p)

            for i, ev in enumerate(events, start=1):
                handler = partial(
                    network.process_event,
                    url=ev["link"],
                    url_num=i,
                    context=context,
                    timeout=5,
                    log=log,
                )

                url = await network.safe_process(
                    handler,
                    url_num=i,
                    log=log,
                )

                sport, event, logo, link = (
                    ev["sport"],
                    ev["event"],
                    ev["logo"],
                    ev["link"],
                )

                key = f"[{sport}] {event} ({TAG})"

                tvg_id, pic = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": url,
                    "logo": logo or pic,
                    "base": "https://storytrench.net/",
                    "timestamp": now,
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                cached_urls[key] = entry

                if url:
                    valid_count += 1
                    urls[key] = entry

            await browser.close()

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")
    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
