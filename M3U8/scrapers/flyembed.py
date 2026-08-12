import asyncio
import re
from collections.abc import KeysView
from functools import partial

from playwright.async_api import Browser, Page

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FLYEMBD"

CACHE_FILE = Cache(TAG, exp=7_200)

API_FILE = Cache(f"{TAG}-api", exp=19_800)


def clean_name(s: str) -> str:
    return re.sub(r"(\r|\n)", "", s).strip()


# def clean_m3u(s: str) -> str:
#     return re.sub(r"\.live\n", ".pro", s)


async def process_event(
    url: str,
    url_num: int,
    page: Page,
    timeout: int | float = 10,
) -> str | None:

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

        iframe = page.locator("iframe").first

        iframe_src = await iframe.get_attribute("src", timeout=1_500)

        wait_task = asyncio.create_task(got_one.wait())

        try:
            await asyncio.wait_for(wait_task, timeout=timeout)
        except TimeoutError:
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
            return captured[0], iframe_src

    except Exception as e:
        log.warning(f"URL {url_num}) {e}")
        return nones

    finally:
        page.remove_listener("request", handler)


async def get_events(cached_keys: KeysView[str]) -> list[Event]:
    now = Time.clean(Time.now())

    events: list[Event] = []

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(
            "https://ovogoal.cyou/api/v2/flyembed.json",
            log=log,
        ):
            api_data: list[dict[str, str]] = r.json()

            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(minutes=30)

    for event_group in api_data:
        if not all(
            values := [
                event_group.get(x)
                for x in (
                    "League",
                    "Team 1 ",
                    "Team2",
                    "Date",
                    "Time",
                    "iframeURL",
                )
            ]
        ):
            continue

        sport, away, home, date, time, link = values

        event_dt = Time.from_str(f"{date} {time}", tz_name="UTC")

        if not start_dt <= event_dt <= end_dt:
            continue

        sport, name = clean_name(sport), clean_name(f"{away} vs {home}")

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        events.append(
            Event(
                sport=sport,
                name=name,
                link=link,
                timestamp=now.timestamp(),
            )
        )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["source"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://flyembed.xyz"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        process_event,
                        url=ev.link,
                        url_num=i,
                        page=page,
                    )

                    source, iframe = await network.safe_process(
                        handler,
                        url_num=i,
                        timeout_return=(None, None),
                        semaphore=network.PW_S,
                        log=log,
                    )

                    key = f"[{ev.sport}] {ev.name} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

                    entry = {
                        "source": source,
                        "logo": logo,
                        "refer": iframe,
                        "timestamp": ev.timestamp,
                        "tvg-id": tvg_id or "Live.Event.us",
                        "link": ev.link,
                    }

                    cached_urls[key] = entry

                    if source:
                        valid_count += 1

                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
