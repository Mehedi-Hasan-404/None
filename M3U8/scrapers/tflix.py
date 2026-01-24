import asyncio
from functools import partial
from urllib.parse import urljoin

import feedparser
from playwright.async_api import Browser, Error, Page, TimeoutError

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TFLIX"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://tv.tflix.app/"

SPORT_ENDPOINTS = ["football", "nba", "nfl", "nhl"]


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=15_000,
        )

        try:
            iframe = await page.wait_for_selector(
                "iframe.metaframe.rptss",
                timeout=3_500,
            )
        except TimeoutError:
            log.warning(f"URL {url_num}) No iframe element.")

            return None, None

        if (old_src := await iframe.get_attribute("src")) and old_src.startswith(
            "https://kloxmkhs.site/stream"
        ):
            new_src = old_src

        else:
            try:
                option = await page.wait_for_selector(
                    'li.dooplay_player_option >> span.title:has-text("TFLIX HD - iOS")',
                    timeout=3_000,
                )

                await option.scroll_into_view_if_needed()

                await option.evaluate("el => el.click()")

                await page.wait_for_function(
                    """
                    (oldSrc) => {
                        const iframe = document.querySelector('iframe.metaframe.rptss');
                        return iframe && iframe.src && iframe.src !== oldSrc;
                    };
                    """,
                    arg=old_src,
                    timeout=5_000,
                )

                iframe_2 = await page.wait_for_selector("iframe.metaframe.rptss")

                if not iframe_2 or not (new_src := await iframe_2.get_attribute("src")):
                    log.warning(f"URL {url_num}) No iframe source.")

                    return None, None
            except TimeoutError:
                log.warning(f"URL {url_num}) No valid TFLIX source.")

                return None, None

        try:
            await page.goto(
                new_src,
                wait_until="domcontentloaded",
                timeout=10_000,
                referer=url,
            )
        except Error:
            log.warning(
                f"URL {url_num}) HTTP 403/404 error while redirecting to iframe source."
            )

            return None, None

        try:
            play_btn = await page.wait_for_selector(
                'button[data-url][onclick*="startPlcb"]',
                timeout=5_000,
            )
        except TimeoutError:
            log.warning(f"URL {url_num}) No play button found.")

            return None, None

        if not (data_url := await play_btn.get_attribute("data-url")):
            log.warning(f"URL {url_num}) No PBID found.")

            return None, None

        log.info(f"URL {url_num}) Captured M3U8")

        return (
            f"https://kloxmkhs.site/stream/stream.m3u8?id={data_url}&format=.m3u8",
            new_src,
        )

    except Exception as e:
        log.warning(f"URL {url_num}) Exception while processing: {e}")

        return None, None


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    tasks = [
        network.request(urljoin(BASE_URL, f"genre/{sport}/feed"), log=log)
        for sport in SPORT_ENDPOINTS
    ]

    results = await asyncio.gather(*tasks)

    events = []

    if not (feeds := [feedparser.parse(html.content) for html in results if html]):
        return events

    for feed in feeds:
        title: str = feed["feed"]["title"]

        sport = title.split("Archives")[0].strip()

        for entry in feed.entries:
            if not (link := entry.get("link")):
                continue

            if not (title := entry.get("title")):
                continue

            if f"[{sport}] {title} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": title,
                    "link": link,
                }
            )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(cached_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now()).timestamp()

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=ev["link"],
                        url_num=i,
                        page=page,
                    )

                    url, iframe = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                        timeout=20,
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
                        "base": iframe,
                        "timestamp": now,
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
