from functools import partial

from playwright.async_api import Browser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=28_800)

BASE_URL = "https://backend.streamcenter.live/api/Parties"

CATEGORIES = {
    4: "Basketball",
    9: "Football",
    13: "Baseball",
    # 14: "American Football",
    15: "Motor Sport",
    16: "Hockey",
    17: "Fight MMA",
    18: "Boxing",
    20: "WWE",
    21: "Tennis",
}


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(
            BASE_URL,
            log=log,
            params={"pageNumber": 1, "pageSize": 500},
        ):
            api_data: list[dict] = r.json()

            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(hours=-1)
    end_dt = now.delta(minutes=5)

    for stream_group in api_data:
        category_id: int = stream_group.get("categoryId")

        name: str = stream_group.get("gameName")

        iframe: str = stream_group.get("videoUrl")

        event_time: str = stream_group.get("beginPartie")

        if not (name and category_id and iframe and event_time):
            continue

        if not (sport := CATEGORIES.get(category_id)):
            continue

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        event_dt = Time.from_str(event_time, timezone="CET")

        if not start_dt <= event_dt <= end_dt:
            continue

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": iframe.replace("<", "?", count=1),
                "timestamp": event_dt.timestamp(),
            }
        )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://streamcenter.xyz"')

    events = await get_events(cached_urls.keys())

    if events:
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
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
                            "base": "https://streamcenter.xyz",
                            "timestamp": ts,
                            "id": tvg_id or "Live.Event.us",
                            "link": link,
                        }

                        urls[key] = cached_urls[key] = entry

    if new_count := len(cached_urls) - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
