from collections.abc import KeysView
from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "CDNTV"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_URL = "https://api.cdnlivetv.is"


async def get_events(cached_keys: KeysView[str]) -> list[Event]:
    now = Time.clean(Time.now())

    events: list[Event] = []

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(
            urljoin(API_URL, "api/v1/events/sports"),
            params={
                "user": "cdnlivetv",
                "plan": "free",
            },
            log=log,
        ):
            api_data = r.json().get("cdn-live-tv")

        API_FILE.write(api_data)

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    sports = [key for key in api_data.keys() if not key.islower()]

    for sport in sports:
        event_info = api_data[sport]

        for event in event_info:
            t1, t2 = (event.get(x) for x in ["awayTeam", "homeTeam"])

            if not (t1 and t2):
                continue

            name = f"{t1} vs {t2}"

            league = event["tournament"]

            if f"[{league}] {name} ({TAG})" in cached_keys:
                continue

            event_dt = Time.from_str(event["start"], tz_name="UTC")

            if not start_dt <= event_dt <= end_dt:
                continue

            if not (channels := event.get("channels")):
                continue

            event_links: list[str] = [channel["url"] for channel in channels]

            events.append(
                Event(
                    sport=league,
                    name=name,
                    link=event_links[0],
                    timestamp=event_dt.timestamp(),
                )
            )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["source"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{API_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=ev.link,
                        url_num=i,
                        page=page,
                        log=log,
                    )

                    source = await network.safe_process(
                        handler,
                        url_num=i,
                        semaphore=network.PW_S,
                        log=log,
                    )

                    key = f"[{ev.sport}] {ev.name} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

                    entry = {
                        "source": source,
                        "logo": logo,
                        "refer": ev.link,
                        "timestamp": ev.timestamp,
                        "tvg-id": tvg_id or "Live.Event.us",
                    }

                    cached_urls[key] = entry

                    if source:
                        valid_count += 1

                        urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
