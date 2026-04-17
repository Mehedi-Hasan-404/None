import ast
import base64
import re
from functools import partial

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STP"

CACHE_FILE = Cache(TAG, exp=28_800)

API_URL = "https://streamtpnew.com/eventos.json"


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    digit_func_ptrn = re.compile(r"{return\s+(\d*);}", re.I)

    if not (digit_list := digit_func_ptrn.findall(event_data.text)):
        log.warning(f"URL {url_num}) Unable to decode url.")
        return

    embed_list_ptrn = re.compile(r"\w*=\[\[(.*)\]\];")

    if not (embed_list := embed_list_ptrn.search(event_data.text)):
        log.warning(f"URL {url_num}) Unable to decode url.")
        return

    embed_list_str = embed_list[0].split("=", 1)[-1].strip(";")

    embed_list: list[tuple[int, str]] = ast.literal_eval(embed_list_str)

    m3u8 = "".join(
        chr(
            int("".join(c for c in base64.b64decode(v).decode("utf-8") if c.isdigit()))
            - sum(map(int, digit_list))
        )
        for _, v in sorted(embed_list, key=lambda i: i[0])
    )

    log.info(f"URL {url_num}) Captured M3U8")

    return m3u8.split("ip=")[0]


async def get_events() -> list[dict[str, str]]:
    events = []

    if not (api_req := await network.request(API_URL, log=log)):
        return events

    elif not (api_data := api_req.json()):
        return events

    for event in api_data:
        name = event.get("title")

        link = event.get("link")

        if not (name and link):
            continue

        if (sport := event.get("category")) and sport == "Other":
            sport = "Live Event"

        events.append(
            {
                "sport": sport,
                "event": name,
                "link": link,
            }
        )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["url"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info('Scraping from "https://streamtpnew.com"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            sport, event = ev["sport"], ev["event"]

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

            cached_urls[key] = entry

            if url:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
