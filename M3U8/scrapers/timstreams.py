from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TIMSTRMS"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_URL = "https://timstreams.fit/api/live-upcoming"

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


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(API_URL, log=log):
            api_data: dict = r.json()

            api_data["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    for info in api_data.get("events", []):
        if (genre := info.get("genre", 999)) not in SPORT_GENRES:
            continue

        event_time = " ".join(info["time"].split("T"))

        event_dt = Time.from_str(event_time, timezone="EST")

        if not start_dt <= event_dt <= end_dt:
            continue

        name: str = info["name"]

        url_id: str = info["url"]

        logo: str | None = info.get("logo")

        sport = SPORT_GENRES[genre]

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        if not (streams := info.get("streams")) or not (url := streams[0].get("url")):
            continue

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": urljoin(BASE_URL, f"watch/{url_id}"),
                "ref": url,
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

                    sport, event, logo, ref, ts = (
                        ev["sport"],
                        ev["event"],
                        ev["logo"],
                        ev["ref"],
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, pic = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo or pic,
                        "base": ref,
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
