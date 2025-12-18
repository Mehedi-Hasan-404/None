from urllib.parse import urljoin

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMFREE"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=19_800)

BASE_URL = "https://streamfree.to/"


async def get_events() -> dict[str, dict[str, str | float]]:
    events = {}

    if not (
        r := await network.request(
            urljoin(BASE_URL, "streams"),
            log=log,
        )
    ):
        return events

    api_data: dict = r.json()

    now = Time.clean(Time.now())

    for streams in api_data.get("streams", {}).values():
        if not streams:
            continue

        for stream in streams:
            sport, name, stream_key = (
                stream.get("league"),
                stream.get("name"),
                stream.get("stream_key"),
            )

            if not (sport and name and stream_key):
                continue

            key = f"[{sport}] {name} ({TAG})"

            logo = (
                urljoin(BASE_URL, thumbnail)
                if (thumbnail := stream.get("thumbnail_url"))
                else None
            )

            tvg_id, pic = leagues.get_tvg_info(sport, name)

            events[key] = {
                "url": network.build_proxy_url(
                    tag=TAG,
                    path=f"{stream_key}/index.m3u8",
                    query={"stream_name": name},
                ),
                "logo": logo or pic,
                "base": BASE_URL,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
            }

    return events


async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    urls.update(await get_events())

    CACHE_FILE.write(urls)

    log.info(f"Collected and cached {len(urls)} new event(s)")
