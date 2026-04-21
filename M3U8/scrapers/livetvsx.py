import asyncio
from functools import partial

from playwright.async_api import Browser, Page, TimeoutError
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "LTVSX"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = "https://livetv.sx/export/webmasters.php"


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
            timeout=10_000,
        )

        if not resp or resp.status != 200:
            log.warning(
                f"URL {url_num}) Status Code: {resp.status if resp else 'None'}"
            )
            return

        try:
            event_a = page.locator('a[title*="Aliez"]').first

            href = await event_a.get_attribute("href", timeout=1_250)
        except TimeoutError:
            log.warning(f"URL {url_num}) No valid sources found.")
            return

        event_url = href if href.startswith("http") else f"https:{href}"

        await page.goto(
            event_url,
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
        log.warning(f"URL {url_num}) {e}")
        return

    finally:
        page.remove_listener("request", handler)


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    php_data = await network.unvd_client.get(BASE_URL, params={"lang": "en"})

    if php_data.status_code != 200:
        return events

    soup = HTMLParser(php_data.content)

    if not (table := soup.css_first("table.tbl")):
        return events

    for row in table.css("tr > td"):
        if not (event_tbl := row.css_first("table")):
            continue

        sport_elem = event_tbl.css_first(".spr")
        league_elem = event_tbl.css_first(".cmp")
        link_elem = event_tbl.css_first("a.title")
        event_id_elem = row.css_first("div[id^='el']")

        if not (league_elem and sport_elem and link_elem and event_id_elem):
            continue

        elif not (event_id := event_id_elem.attributes.get("id")):
            continue

        sport = sport_elem.text(strip=True)
        league = league_elem.text(strip=True)
        event_name = link_elem.text(strip=True)

        if f"[{sport} - {league}] {event_name} ({TAG})" in cached_keys:
            continue

        events.append(
            {
                "sport": sport,
                "league": league,
                "event": event_name,
                "link": f"https://cdn.livetv872.me/cache/links/en.{event_id[2:]}.html",
            }
        )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://livetv.sx/enx/"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

        async with network.event_context(browser, ignore_https=True) as context:
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
                        timeout=20,
                    )

                    sport, league, event = (
                        ev["sport"],
                        ev["league"],
                        ev["event"],
                    )

                    key = f"[{sport} - {league}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://livetv.sx/enx/",
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
