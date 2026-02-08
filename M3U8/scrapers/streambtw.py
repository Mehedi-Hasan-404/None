import base64
import re
from functools import partial
from urllib.parse import urljoin

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMBTW"

CACHE_FILE = Cache(TAG, exp=3_600)

API_FILE = Cache(f"{TAG}-api", exp=28_800)

BASE_URL = "https://hiteasport.info"


def fix_league(s: str) -> str:
    pattern = re.compile(r"^\w*-\w*", re.IGNORECASE)

    return " ".join(s.split("-")) if pattern.search(s) else s


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        return

    valid_m3u8 = re.compile(r'var\s+(\w+)\s*=\s*"([^"]*)"', re.IGNORECASE)

    if not (match := valid_m3u8.search(html_data.text)):
        log.info(f"URL {url_num}) No M3U8 found")
        return

    stream_link: str = match[2]

    if not stream_link.startswith("http"):
        stream_link = base64.b64decode(stream_link).decode("utf-8")

    log.info(f"URL {url_num}) Captured M3U8")

    return stream_link


async def get_events() -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (api_data := API_FILE.load(per_entry=False)):
        log.info("Refreshing API cache")

        api_data = {"timestamp": now.timestamp()}

        if r := await network.request(
            urljoin(BASE_URL, "public/api.php"),
            log=log,
            params={"action": "get"},
        ):
            api_data: dict = r.json()

            api_data["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    events = []

    if last_update := api_data.get("updated_at"):
        last_update_dt = Time.from_str(last_update, timezone="UTC")

        if last_update_dt.date() != now.date():
            return events

    for info in api_data.get("groups", []):
        if not (sport := info["title"]):
            sport = "Live Event"

        if items := info.get("items"):
            for event in items:
                event_name: str = event["title"]

                link: str = event["url"]

                events.append(
                    {
                        "sport": fix_league(sport),
                        "event": event_name,
                        "link": link,
                    }
                )

    return events


async def scrape() -> None:
    if cached := CACHE_FILE.load():
        urls.update(cached)

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    events = await get_events()

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev["link"],
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            if url:
                sport, event, link = (
                    ev["sport"],
                    ev["event"],
                    ev["link"],
                )

                key = f"[{sport}] {event} ({TAG})"

                tvg_id, logo = leagues.get_tvg_info(sport, event)

                entry = {
                    "url": url,
                    "logo": logo,
                    "base": link,
                    "timestamp": now.timestamp(),
                    "id": tvg_id or "Live.Event.us",
                    "link": link,
                }

                urls[key] = entry

    log.info(f"Collected {len(urls)} event(s)")

    CACHE_FILE.write(urls)
