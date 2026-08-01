import json
import re
from functools import partial
from typing import KeysView

from selectolax.parser import HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "FLYEMBD"

CACHE_FILE = Cache(TAG, exp=10_800)

API_FILE = Cache(f"{TAG}-api", exp=19_800)


def clean_name(s: str) -> str:
    return re.sub(r"(\r|\n)", "", s).strip()


def clean_m3u(s: str) -> str:
    return re.sub(r"\.live\n", ".pro", s)


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (event_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return nones

    soup = HTMLParser(event_data.content)

    ifr = soup.css_first("iframe")

    if not ifr or not (src := ifr.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return nones

    ifr_src = network.ensure_https(src)

    if not (
        ifr_src_data := await network.request(
            ifr_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return nones

    valid_m3u8 = re.compile(
        r"(file|source|streamUrl)\s*(:|=)\s+(\'|\")([^\"]*)(\'|\")",
        re.I,
    )

    if not (match := valid_m3u8.search(ifr_src_data.text)):
        log.warning(f"URL {url_num}) No source found.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return json.loads(f'"{match[4]}"'), ifr_src


async def get_events(cached_keys: KeysView[str]) -> list[Event]:
    now = Time.clean(Time.now())

    events: list[Event] = []

    if not (api_data := API_FILE.load(per_entry=False, index=-1)):
        log.info("Refreshing API cache")

        api_data = [{"timestamp": now.timestamp()}]

        if r := await network.request(
            "https://ovogoal.cyou/api/v2/flyembed.json",
            log=log,
        ):
            api_data: list[dict[str, str]] = r.json()

            api_data[-1]["timestamp"] = now.timestamp()

        API_FILE.write(api_data)

    start_dt = now.delta(hours=-3)
    end_dt = now.delta(minutes=30)

    for event_group in api_data:
        if not all(
            values := [
                event_group.get(x)
                for x in (
                    "League",
                    "Team 1 ",
                    "Team2",
                    "Date",
                    "Time",
                    "iframeURL",
                )
            ]
        ):
            continue

        sport, away, home, date, time, link = values

        event_dt = Time.from_str(
            re.sub(
                r"\s?(A\.?M\.?|P\.?M\.?)",
                "",
                f"{date} {time}",
                flags=re.I,
            ),
            timezone="UTC",
        )

        if not start_dt <= event_dt <= end_dt:
            continue

        sport, name = clean_name(sport), clean_name(f"{away} vs {home}")

        if f"[{sport}] {name} ({TAG})" in cached_keys:
            continue

        events.append(
            Event(
                sport=sport,
                name=name,
                link=link,
                timestamp=now.timestamp(),
            )
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["source"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://flyembed.xyz"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            source, iframe = await network.safe_process(
                handler,
                url_num=i,
                timeout_return=(None, None),
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "source": source,
                "logo": logo,
                "refer": iframe,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
                "link": ev.link,
            }

            cached_urls[key] = entry

            if source:
                valid_count += 1

                entry["source"] = clean_m3u(source)

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
