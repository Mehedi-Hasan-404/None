import asyncio
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMHUB"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://livesports4u.net"

SPORT_ENDPOINTS = [
    f"sport_{sport_id}"
    for sport_id in [
        # "68c02a4465113",  # American Football
        # "68c02a446582f",  # Baseball
        "68c02a4466011",  # Basketball
        "68c02a4466f56",  # Hockey
        # "68c02a44674e9",  # MMA
        # "68c02a4467a48",  # Racing
        "68c02a4464a38",  # Soccer
        # "68c02a4468cf7",  # Tennis
    ]
]


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
            btn = page.locator("button.btn.btn-sm.btn-success.streamLink")

            iframe_src = await btn.get_attribute("data-src", timeout=1_250)
        except TimeoutError:
            log.warning(f"URL {url_num}) No iframe source found.")
            return

        await page.goto(
            iframe_src,
            wait_until="domcontentloaded",
            timeout=5_000,
        )

        wait_task = asyncio.create_task(got_one.wait())

        try:
            await asyncio.wait_for(wait_task, timeout=5)
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
        log.warning(f"URL {url_num}) {e}")

        return

    finally:
        page.remove_listener("request", handler)


async def get_events() -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    tasks = [
        network.request(
            urljoin(BASE_URL, f"events/{date}"),
            params={"sport_id": sport_id},
            log=log,
        )
        for date in [now.date(), now.delta(days=1).date()]
        for sport_id in SPORT_ENDPOINTS
    ]

    results = await asyncio.gather(*tasks)

    events = []

    if not (soups := [HTMLParser(html.content) for html in results if html]):
        return events

    for soup in soups:
        for section in soup.css(".events-section"):
            if not (sport_node := section.css_first(".section-titlte")):
                continue

            sport = sport_node.text(strip=True)

            for event in section.css(".section-event"):
                event_name = "Live Event"

                if teams := event.css_first(".event-competitors"):
                    home, away = teams.text(strip=True).split("vs.")

                    event_name = f"{away} vs {home}"

                if not (event_button := event.css_first(".event-button a")) or not (
                    href := event_button.attributes.get("href")
                ):
                    continue

                event_date = event.css_first(".event-countdown").attributes.get(
                    "data-start"
                )

                event_dt = Time.from_str(event_date, timezone="UTC")

                if event_dt.date() != now.date():
                    continue

                events.append(
                    {
                        "sport": sport,
                        "event": event_name,
                        "link": href,
                        "timestamp": now.timestamp(),
                    }
                )

    return events


async def scrape(browser: Browser) -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["url"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} new URL(s)")

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

                    sport, event, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://hardsmart.click",
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        entry["url"] = url.split("?st")[0]

                        urls[key] = entry

        log.info(f"Collected and cached {len(urls)} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
