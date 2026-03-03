import asyncio
import re
from functools import partial
from itertools import chain
from typing import Any
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, TimeoutError

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "WATCHFTY"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_URL = "https://api.watchfooty.st"

BASE_MIRRORS = [
    "https://www.watchfooty.top",
    "https://www.watchfooty.st",
    "https://www.watchfooty.su",
]

VALID_SPORTS = [
    # "american-football",
    "baseball",
    "basketball",
    "fighting",
    "football",
    "golf",
    "hockey",
    "racing",
    "tennis",
    "volleyball",
]


async def refresh_api_cache(now: Time) -> list[dict[str, Any]]:
    log.info("Refreshing API cache")

    tasks = [
        network.request(
            urljoin(API_URL, "api/v1/matches/all"),
            log=log,
            params={"date": d.date()},
        )
        for d in [now, now.delta(days=1)]
    ]

    results = await asyncio.gather(*tasks)

    if not (data := [*chain.from_iterable(r.json() for r in results if r)]):
        return [{"timestamp": now.timestamp()}]

    for ev in data:
        ev["ts"] = ev.pop("timestamp")

        data[-1]["timestamp"] = now.timestamp()

    return data


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:

    nones = None, None

    pattern = re.compile(r"\((\d+)\)")

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
            timeout=8_000,
        )

        if resp.status != 200:
            log.warning(f"URL {url_num}) status code: {resp.status}")
            return

        await page.wait_for_timeout(2_000)

        try:
            header = await page.wait_for_selector("text=/Stream Links/i", timeout=4_000)

            text = await header.inner_text()
        except TimeoutError:
            log.warning(f"URL {url_num}) Can't find stream links header.")

            return nones

        if not (match := pattern.search(text)) or int(match[1]) == 0:
            log.warning(f"URL {url_num}) No available stream links.")

            return nones

        try:
            first_available = await page.wait_for_selector(
                'a[href*="/stream/"]',
                timeout=3_000,
            )
        except TimeoutError:
            log.warning(f"URL {url_num}) No available stream links.")

            return nones

        if not (href := await first_available.get_attribute("href")):
            log.warning(f"URL {url_num}) No available stream links.")

            return nones

        embed = re.sub(
            pattern=r"^.*\/stream",
            repl="https://spiderembed.top/embed",
            string=href,
        )

        await page.goto(
            embed,
            wait_until="domcontentloaded",
            timeout=5_000,
        )

        wait_task = asyncio.create_task(got_one.wait())

        try:
            await asyncio.wait_for(wait_task, timeout=6)
        except asyncio.TimeoutError:
            log.warning(f"URL {url_num}) Timed out waiting for M3U8.")

            return nones

        finally:
            if not wait_task.done():
                wait_task.cancel()

                try:
                    await wait_task
                except asyncio.CancelledError:
                    pass

        if captured:
            log.info(f"URL {url_num}) Captured M3U8")

            return captured[0], embed

        log.warning(f"URL {url_num}) No M3U8 captured after waiting.")

        return nones

    except Exception as e:
        log.warning(f"URL {url_num}) {e}")

        return nones

    finally:
        page.remove_listener("request", handler)


async def get_events(base_url: str, cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        api_data = await refresh_api_cache(now)

        API_FILE.write(api_data)

    events = []

    pattern = re.compile(r"\-+|\(")

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=5)

    for event in api_data:
        match_id = event.get("matchId")

        name = event.get("title")

        league = event.get("league")

        if not (match_id and name and league):
            continue

        if event["sport"] not in VALID_SPORTS:
            continue

        sport = pattern.split(league, 1)[0].strip()

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        if not (date := event.get("date")):
            continue

        event_dt = Time.from_str(date, timezone="UTC")

        if not start_dt <= event_dt <= end_dt:
            continue

        logo = urljoin(API_URL, poster) if (poster := event.get("poster")) else None

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": urljoin(base_url, f"stream/{match_id}"),
                "logo": logo,
                "timestamp": event_dt.timestamp(),
            }
        )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    if not (base_url := await network.get_base(BASE_MIRRORS)):
        log.warning("No working Watch Footy mirrors")

        CACHE_FILE.write(cached_urls)

        return

    log.info(f'Scraping from "{base_url}"')

    if events := await get_events(base_url, cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=(link := ev["link"]),
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

                    sport, event, logo, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": iframe,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1

                        entry["url"] = url.split("&t")[0]

                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
