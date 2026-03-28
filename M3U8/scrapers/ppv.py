import re
from functools import partial

from playwright.async_api import Browser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "PPV"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_MIRRORS = [
    "https://api.ppv.to/api/streams",
    "https://api.ppv.cx/api/streams",
    "https://api.ppv.sh/api/streams",
    "https://api.ppv.la/api/streams",
]


def fix_url(s: str) -> str:
    pattern = re.compile(r"index\.m3u8$", re.I)

    return pattern.sub(r"tracks-v1a1/mono.ts.m3u8", s)


async def get_events(url: str, cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(url, log=log):
            api_data: dict = r.json()

        API_FILE.write(api_data)

    events = []

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    for stream_group in api_data.get("streams", []):
        sport = stream_group["category"]

        if sport == "24/7 Streams":
            continue

        for event in stream_group.get("streams", []):
            name = event.get("name")

            start_ts = event.get("starts_at")

            logo = event.get("poster")

            iframe = event.get("iframe")

            if not (name and start_ts and iframe):
                continue

            if f"[{sport}] {name} ({TAG})" in cached_keys:
                continue

            event_dt = Time.from_ts(start_ts)

            if not start_dt <= event_dt <= end_dt:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": name,
                    "link": f"{iframe}#player=clappr#autoplay=true",
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

    if not (api_url := await network.get_base(API_MIRRORS)):
        log.warning("No working PPV mirrors")

        CACHE_FILE.write(cached_urls)

        return

    log.info(f'Scraping from "{api_url}"')

    if events := await get_events(api_url, cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        timeout=6,
                        log=log,
                    )

                    url = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
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
                        "base": link,
                        "timestamp": ts,
                        "id": tvg_id or "Live.Event.us",
                        "link": link,
                    }

                    cached_urls[key] = entry

                    if url:
                        valid_count += 1

                        entry["url"] = fix_url(url)

                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
