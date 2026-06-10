import json
import re
from functools import partial
from urllib.parse import parse_qsl, urlsplit

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "VIVATOPS"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URL = "https://vivatops.cyou"


async def process_event(channel_id: str, url_num: int) -> str | None:
    nones = None, None

    ifr_url, ref_url = (
        f"https://edher.lockedherhe.site/player_stateless/channel{channel_id}",
        f"{BASE_URL}/vivo/?ch={channel_id}",
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

    valid_m3u8 = re.compile(r'streamUrl\s+=\s+"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(html_data.text)):
        log.warning(f"URL {url_num}) No M3U8 found")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return json.loads(f'"{match[1]}"'), ref_url


async def get_events() -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    if not (last_update := soup.css_first("h2.update")):
        return events

    elif (
        now.strftime("%d-%m-%y")
        != last_update.text(strip=True).split(":")[-1].split()[0]
    ):
        return events

    sport = "Live Event"

    for matches in soup.css(".match"):
        if not (a_elem := matches.css_first("a")) or not (
            href := a_elem.attributes.get("href")
        ):
            continue

        params = dict(parse_qsl(urlsplit(href).query, keep_blank_values=True))

        if not (channel_id := params.get("ch")):
            continue

        for event in matches.css("strong"):
            splits = event.text(strip=True).split()

            event_name = (
                " ".join(splits[: splits.index("-")])
                if "-" in splits
                else " ".join(splits)
            )

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
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

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} new URL(s)")

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

        log.info(f"Collected and cached {len(urls)} new event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
