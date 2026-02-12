import json
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser, Page

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PIXEL"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://pixelsport.tv"


async def get_api_data(page: Page) -> dict[str, list[dict, str, str]]:
    try:
        await page.goto(
            url := urljoin(BASE_URL, "backend/livetv/events"),
            wait_until="domcontentloaded",
            timeout=10_000,
        )

        raw_json = await page.locator("pre").inner_text(timeout=5_000)
    except Exception as e:
        log.error(f'Failed to fetch "{url}": {e}')

        return {}

    return json.loads(raw_json)


async def get_events(page: Page) -> dict[str, dict[str, str | float]]:
    now = Time.clean(Time.now())

    api_data = await get_api_data(page)

    events = {}

    for event in api_data.get("events", []):
        event_dt = Time.from_str(event["date"], timezone="UTC")

        if event_dt.date() != now.date():
            continue

        event_name = event["match_name"]

        channel_info: dict[str, str] = event["channel"]

        category: dict[str, str] = channel_info["TVCategory"]

        sport = category["name"]

        stream_urls = [(i, f"server{i}URL") for i in range(1, 4)]

        for z, stream_url in stream_urls:
            if (stream_link := channel_info.get(stream_url)) and stream_link != "null":
                key = f"[{sport}] {event_name} {z} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event_name)

                events[key] = {
                    "url": stream_link,
                    "logo": logo,
                    "base": BASE_URL,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                }

    return events


async def scrape(browser: Browser) -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    async with network.event_context(browser) as context:
        async with network.event_page(context) as page:
            handler = partial(get_events, page=page)

            events = await network.safe_process(
                handler,
                url_num=1,
                semaphore=network.PW_S,
                log=log,
            )

    urls.update(events or {})

    CACHE_FILE.write(urls)

    log.info(f"Collected and cached {len(urls)} new event(s)")
