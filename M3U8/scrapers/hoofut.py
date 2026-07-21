from collections.abc import KeysView
from urllib.parse import urljoin

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "HOOFUT"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)

BASE_URL = "https://hoofoot.ru"


def get_event_info(name: str) -> tuple[str, str]:
    return (
        tuple(x.strip() for x in name.split(":")[:2])
        if ":" in name
        else ("Live Event", name)
    )


async def get_events(cached_keys: KeysView[str]) -> dict[str, dict[str, str | float]]:
    events = {}

    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(
            urljoin(BASE_URL, "api/events"),
            headers={"Referer": BASE_URL},
            log=log,
        ):
            api_data: dict[str, list[dict]] = r.json()

            api_data["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    for event in api_data.get("events", []):
        if not all(
            values := [
                event.get(k)
                for k in (
                    "Match",
                    "League",
                    "Date",
                    "Time",
                )
            ]
        ):
            continue

        name, sport, event_date, event_time = values

        if sport.lower() == "unknown league":
            sport, name = get_event_info(name)

        event_dt = Time.from_str(f"{event_date} {event_time}", timezone="UTC")

        if event_dt.date() != now.date():
            continue

        elif not (event_channels := event.get("Channels")):
            continue

        event_urls: dict[str, str] = {
            channel["name"]: channel.get("id")
            for channel in event_channels
            if channel.get("id")
            if not channel["name"].lower().startswith("backup")
        }

        for ch_name, ch_id in event_urls.items():
            if (key := f"[{sport}] {name} | {ch_name} ({TAG})") in cached_keys:
                continue

            tvg_id, logo = leagues.get_tvg_info(sport, name)

            events[key] = {
                "source": urljoin(BASE_URL, f"stream/{ch_id}"),
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
