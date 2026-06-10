import base64
import re
from functools import partial
from urllib.parse import urlsplit

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "RESPRTZ"

CACHE_FILE = Cache(TAG, exp=3_600)

API_FILE = Cache(f"{TAG}-api", exp=28_800)

API_URL = "https://streameast.mov/api/events"


async def process_event(channel_id: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    ifr_url, ref_url = (
        f"https://donis.jimpenopisonline.online/premiumtv/resportz.php?id={channel_id}",
        f"https://resportz.cfd/live/stream-{channel_id}.php",
    )

    if not (
        html_data := await network.request(
            ifr_url,
            headers={"Referer": ref_url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load url.")
        return nones

    pattern = re.compile(r'source:\s+window.atob\((\'|\")([^"]*)(\'|\")\)', re.I)

    if not (match := pattern.search(html_data.text)):
        log.warning(f"URL {url_num}) No M3U8 found")
        return nones

    m3u8 = base64.b64decode(match[2]).decode("utf-8")

    log.info(f"URL {url_num}) Captured M3U8")

    return (
        m3u8,  # .replace(urlsplit(m3u8).netloc, "kolis.phantemlis.top"),
        ref_url,
    )


async def get_events() -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    events = []

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(API_URL, log=log):
            api_data: list[dict] = r.json()

            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    if not (date := api_data[0].get("day")):
        return events

    api_date = re.sub(
        r"(?<=\d)(st|nd|rd|th)",
        "",
        date.split("-")[0].strip(),
        flags=re.I,
    )

    if api_date != f"{now:%A} {now.day} {now:%B} {now:%Y}":
        return events

    for category in api_data[0].get("categories", {}):
        if category.lower() in ["popular live events", "tv shows"]:
            continue

        category_info = api_data[0]["categories"][category]

        for event_info in category_info:
            if event_info.get("source") != "tv":
                continue

            if not (channels := event_info.get("channels")):
                continue

            name: str = event_info["event"]

            channel_id: str = channels[0]["channel_id"]

            events.append(
                {
                    "sport": category,
                    "event": name,
                    "channel-id": channel_id,
                    "timestamp": now.timestamp(),
                }
            )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["url"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info('Scraping from "https://streameast.mov"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                channel_id=ev["channel-id"],
                url_num=i,
            )

            url, iframe = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

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
                "base": iframe,
                "timestamp": ts,
                "id": tvg_id or "Live.Event.us",
                "link": iframe,
            }

            cached_urls[key] = entry

            if url:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
