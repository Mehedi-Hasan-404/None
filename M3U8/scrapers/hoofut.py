from collections.abc import KeysView
from urllib.parse import urljoin

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "HOOFUT"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://hoofoot.ru"


async def get_events(cached_keys: KeysView[str]) -> dict[str, dict[str, str | float]]:
    events = {}

    if not (api_req := await network.request(urljoin(BASE_URL, "api/events"), log=log)):
        return events

    elif not (api_data := api_req.json()):
        return events

    now = Time.clean(Time.now())

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    for event in api_data.get("events", []):
        if not all(
            values := [
                event.get(k)
                for k in (
                    "Match",
                    "League",
                    "Date",
                    "Time",
                    "Live",
                )
            ]
        ):
            continue

        name, sport, event_date, event_time, is_live = values

        if sport.lower() == "unknown league":
            continue

        event_dt = Time.from_str(f"{event_date} {event_time}", timezone="UTC")

        if event_dt.date() != now.date():
            continue

        elif (not start_dt <= event_dt <= end_dt) or (not is_live):
            continue

        elif not (event_channels := event.get("Channels")):
            continue

        event_urls: dict[str, str] = {
            channel["name"]: channel.get("id")
            for channel in event_channels
            if channel.get("id")
            if "backup" not in channel["name"]
        }

        for ch_name, ch_id in event_urls.items():
            if (key := f"[{sport}] {name} | {ch_name} ({TAG})") in cached_keys:
                continue

            tvg_id, logo = leagues.get_tvg_info(sport, name)

            events[key] = {
                "source": urljoin(BASE_URL, f"stream?id={ch_id}"),
                "logo": logo,
                "refer": BASE_URL,
                "timestamp": now.timestamp(),
                "tvg-id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_count = len(
        valid_urls := {k: v for k, v in cached_urls.items() if v["source"]}
    )

    urls.update(valid_urls)

    log.info(f"Loaded {valid_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    urls.update(await get_events(cached_urls.keys()))

    (
        log.info(f"Collected and cached {new_count} new event(s)")
        if (new_count := len(urls) - valid_count)
        else log.info("No new events found")
    )

    CACHE_FILE.write(urls)
