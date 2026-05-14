from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "DAMI"

CACHE_FILE = Cache(TAG, exp=28_800)

API_URL = "https://api.ppv.to/api/streams"
# "https://api.ppv.cx/api/streams"
# "https://api.ppv.sh/api/streams"


async def get_events() -> dict[str, dict[str, str | float]]:
    now = Time.clean(Time.now())

    events = {}

    if not (r := await network.request(API_URL, log=log)):
        return events

    api_data: dict[str, dict] = r.json()

    for stream_group in api_data.get("streams", []):
        sport = stream_group["category"]

        if sport == "24/7 Streams":
            continue

        for event in stream_group.get("streams", []):
            name = event.get("name")

            start_ts = event.get("starts_at")

            logo = event.get("poster")

            uri_name = event.get("uri_name")

            if not (name and start_ts and uri_name):
                continue

            event_dt = Time.from_ts(start_ts)

            if event_dt.date() != now.date():
                continue

            key = f"[{sport}] {name} ({TAG})"

            tvg_id, pic = leagues.get_tvg_info(sport, name)

            events[key] = {
                "url": f"https://dami-tv.pro/live-hls/channel/{uri_name}/playlist.m3u8",
                "logo": logo or pic,
                "base": f"https://dami-tv.pro/player/auto/?match={uri_name}",
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update(cached_urls)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{API_URL}"')

    events = await get_events()

    urls.update(events)

    log.info(f"Collected and cached {len(urls)} event(s)")

    CACHE_FILE.write(urls)
