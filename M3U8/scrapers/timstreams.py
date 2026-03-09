import asyncio
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser, Page, Response
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = "https://timstreams.fit"

SPORT_GENRES = {
    1: "Soccer",
    2: "Motorsport",
    3: "MMA",
    4: "Fight",
    5: "Boxing",
    6: "Wrestling",
    7: "Basketball",
    # 8: "American Football",
    9: "Baseball",
    10: "Tennis",
    11: "Hockey",
    # 12: "Darts",
    # 13: "Cricket",
    # 14: "Cycling",
    # 15: "Rugby",
    # 16: "Live Shows",
    # 17: "Other",
}


def sift_xhr(resp: Response) -> bool:
    resp_url = resp.url

    return "hmembeds.one/embed" in resp_url and resp.status == 200


async def process_event(
    url: str,
    url_num: int,
    page: Page,
) -> tuple[str | None, str | None]:

    nones = None, None

    captured: list[str] = []

    got_one = asyncio.Event()

    handler = partial(
        network.capture_req,
        captured=captured,
        got_one=got_one,
    )

    page.on("request", handler)

    try:
        try:
            async with page.expect_response(sift_xhr, timeout=3_000) as strm_resp:
                resp = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=6_000,
                )

                if not resp or resp.status != 200:
                    log.warning(
                        f"URL {url_num}) Status Code: {resp.status if resp else 'None'}"
                    )

                    return nones

                response = await strm_resp.value

                embed_url = response.url
        except TimeoutError:
            log.warning(f"URL {url_num}) No available stream links.")

            return nones

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

            return captured[0], embed_url

        log.warning(f"URL {url_num}) No M3U8 captured after waiting.")

        return nones

    except Exception as e:
        log.warning(f"URL {url_num}) {e}")

        return nones

    finally:
        page.remove_listener("request", handler)


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    for card in soup.css("#eventsSection .card"):
        card_attrs = card.attributes

        if not (sport_id := card_attrs.get("data-genre")):
            continue

        elif not (sport := SPORT_GENRES.get(int(sport_id))):
            continue

        if not (event_name := card_attrs.get("data-search")):
            continue

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        if not (badge_elem := card.css_first(".badge")):
            continue

        if "data-countdown" in badge_elem.attributes:
            continue

        if (not (watch_btn := card.css_first("a.btn-watch"))) or (
            not (href := watch_btn.attributes.get("href"))
        ):
            continue

        logo = None

        if card_thumb := card.css_first(".card-thumb img"):
            logo = card_thumb.attributes.get("src")

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": urljoin(BASE_URL, href),
                "logo": logo,
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

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        now = Time.clean(Time.now())

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
                    )

                    sport, event, logo = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": iframe,
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
