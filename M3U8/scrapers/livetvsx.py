import asyncio
from functools import partial

import feedparser
from playwright.async_api import Browser, Page

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "LIVETVSX"

CACHE_FILE = Cache(TAG, exp=10_800)

XML_CACHE = Cache(f"{TAG}-xml", exp=28_000)

BASE_URL = "https://cdn.livetv861.me/rss/upcoming_en.xml"

VALID_SPORTS = {
    "Football",
    "Basketball",
    "Ice Hockey",
}


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

        await page.wait_for_timeout(1_500)

        buttons = await page.query_selector_all(".lnktbj a[href*='webplayer']")

        labels = await page.eval_on_selector_all(
            ".lnktyt span",
            "elements => elements.map(el => el.textContent.trim().toLowerCase())",
        )

        for btn, label in zip(buttons, labels):
            if label in ["web", "youtube"]:
                continue

            if not (href := await btn.get_attribute("href")):
                continue

            break

        else:
            log.warning(f"URL {url_num}) No valid sources found.")
            return

        href = href if href.startswith("http") else f"https:{href}"

        await page.goto(
            href,
            wait_until="domcontentloaded",
            timeout=5_000,
        )

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


async def refresh_xml_cache(now_ts: float) -> dict[str, dict[str, str | float]]:
    log.info("Refreshing XML cache")

    events = {}

    if not (xml_data := await network.request(BASE_URL, log=log)):
        return events

    feed = feedparser.parse(xml_data.content)

    for entry in feed.entries:
        title = entry.get("title")

        link = entry.get("link")

        sport_sum = entry.get("summary")

        date = entry.get("published")

        if not all([title, link, sport_sum, date]):
            continue

        sprt = sport_sum.split(".", 1)

        sport, league = sprt[0], "".join(sprt[1:]).strip()

        if sport not in VALID_SPORTS:
            continue

        event_dt = Time.from_str(date)

        key = f"[{sport} - {league}] {title} ({TAG})"

        events[key] = {
            "sport": sport,
            "league": league,
            "event": title,
            "link": link,
            "event_ts": event_dt.timestamp(),
            "timestamp": now_ts,
        }

    return events


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (events := XML_CACHE.load()):
        events = await refresh_xml_cache(now.timestamp())

        XML_CACHE.write(events)

    live = []

    start_ts = now.delta(hours=-1).timestamp()
    end_ts = now.delta(minutes=5).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
            continue

        live.append({**v})

    return live


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://livetv.sx/enx/"')

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        async with network.event_context(browser, ignore_https=True) as context:
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

                    sport, league, event, ts, link = (
                        ev["sport"],
                        ev["league"],
                        ev["event"],
                        ev["timestamp"],
                        ev["link"],
                    )

                    key = f"[{sport} - {league}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://livetv.sx/enx/",
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
