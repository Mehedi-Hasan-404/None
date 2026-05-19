from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FOOTFAST"

CACHE_FILE = Cache(TAG, exp=5_400)

API_FILE = Cache(f"{TAG}-api", exp=28_800)

BASE_URL = "https://footfast.cc"

CATEGORIES = {
    1: "Soccer",
    3: "NBA",
    6: "UFC/MMA",
    8: "NHL",
    13: "Live Event",
    17: "MLB",
    10: "Racing",
    21: "Basketball",
    #: "American Football",
    #: "Boxing",
    #: "Rugby",
    #: "Tennis",
    #: "Golf",
}


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(urljoin(BASE_URL, "api/public/catalog"), log=log):
            api_data: dict[str, list[dict]] = r.json()

            api_data["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    start_ts = now.delta(hours=-3).timestamp()

    for event_info in api_data.get("events", []):
        event_name: str = event_info.get("name")
        category_id: int = event_info.get("category_id")

        event_ts: int = event_info.get("start")

        if not (event_name and category_id and event_ts):
            continue

        if not start_ts <= event_ts <= now.timestamp():
            continue

        # if not (sources := event_info.get("source")):
        #     continue

        # elif not (source_id := sources[0].get("id")):
        #     continue

        if not (sport := CATEGORIES.get(category_id)):
            continue

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        embed_id: str = event_info["embedId"]

        events.append(
            {
                "sport": sport,
                "event": event_name,
                # "link": f"https://aerastora.com/event/{embed_id}?source={source_id}",
                "link": urljoin(BASE_URL, f"event/{embed_id}"),
                "timestamp": event_ts,
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

                    sport, event, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["timestamp"],
                    )

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    key = f"[{sport}] {event} ({TAG})"

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://aerastora.com/ ",
                        "timestamp": ts,
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
