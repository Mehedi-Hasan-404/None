from functools import partial
from urllib.parse import urljoin

from playwright.async_api import Browser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "CDNTV"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

API_URL = "https://api.cdn-live.tv"


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    events = []

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        if r := await network.request(
            urljoin(API_URL, "api/v1/events/sports"),
            log=log,
            params={"user": "cdnlivetv", "plan": "free"},
        ):
            api_data = r.json().get("cdn-live-tv", {"timestamp": now.timestamp()})

        API_FILE.write(api_data)

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    sports = [key for key in api_data.keys() if not key.islower()]

    for sport in sports:
        event_info = api_data[sport]

        for event in event_info:
            t1, t2 = event.get("awayTeam"), event.get("homeTeam")

            if not (t1 and t2):
                continue

            name = f"{t1} vs {t2}"

            league = event["tournament"]

            if f"[{league}] {name} ({TAG})" in cached_keys:
                continue

            event_dt = Time.from_str(event["start"], timezone="UTC")

            if not start_dt <= event_dt <= end_dt:
                continue

            if not (channels := event.get("channels")):
                continue

            event_links: list[str] = [channel["url"] for channel in channels]

            # if not (
            #     link := (
            #         event_links[0]
            #         if len(event_links) == 1
            #         else await network.get_base(event_links)
            #     )
            # ):
            #     continue

            link = event_links[0]

            events.append(
                {
                    "sport": league,
                    "event": name,
                    "link": link,
                    "timestamp": event_dt.timestamp(),
                }
            )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    cached_count = len(cached_urls)

    urls.update(cached_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{API_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser) as context:
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
                            "base": link,
                            "timestamp": ts,
                            "id": tvg_id or "Live.Event.us",
                            "link": link,
                        }

                        urls[key] = cached_urls[key] = entry

        log.info(f"Collected and cached {len(cached_urls) - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
