import re

import httpx

from .utils import Cache, Time, get_logger, leagues

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TVP"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=86_400)

BASE_URL = "https://tvpass.org/playlist/m3u"


async def get_data(client: httpx.AsyncClient) -> list[str]:
    try:
        r = await client.get(BASE_URL)
        r.raise_for_status()
    except Exception as e:
        log.error(f'Failed to fetch "{BASE_URL}": {e}')

        return []

    return r.text.splitlines()


async def get_events(client: httpx.AsyncClient) -> dict[str, dict[str, str | float]]:
    now = Time.clean(Time.now()).timestamp()

    events = {}

    data = await get_data(client)

    for i, line in enumerate(data, start=1):
        if line.startswith("#EXTINF"):
            tvg_id_match = re.search(r'tvg-id="([^"]*)"', line)
            tvg_name_match = re.search(r'tvg-name="([^"]*)"', line)
            group_title_match = re.search(r'group-title="([^"]*)"', line)

            tvg = tvg_id_match[1] if tvg_id_match else None

            if not tvg and (url := data[i]).endswith("/sd"):
                if tvg_name := tvg_name_match[1]:
                    sport = group_title_match[1].upper().strip()

                    event = "(".join(tvg_name.split("(")[:-1]).strip()

                    key = f"[{sport}] {event} ({TAG})"

                    channel = url.split("/")[-2]

                    tvg_id, logo = leagues.info(sport)

                    events[key] = {
                        "url": f"http://origin.thetvapp.to/hls/{channel}/mono.m3u8",
                        "logo": logo,
                        "id": tvg_id or "Live.Event.us",
                        "base": "https://tvpass.org",
                        "timestamp": now,
                    }

    return events


async def scrape(client: httpx.AsyncClient) -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)
        log.info(f"Loaded {len(urls)} event(s) from cache")
        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events(client)

    urls.update(events)

    CACHE_FILE.write(urls)

    log.info(f"Collected and cached {len(urls)} new event(s)")
