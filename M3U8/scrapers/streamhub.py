import asyncio
import re
from functools import partial
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMHUB"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_CACHE = Cache(f"{TAG}-html", exp=19_800)

BASE_URL = "https://livesports4u.net"

SPORT_ENDPOINTS = [
    f"sport_{sport_id}"
    for sport_id in [
        # "68c02a4465113",  # American Football
        # "68c02a446582f",  # Baseball
        "68c02a4466011",  # Basketball
        "68c02a4466f56",  # Hockey
        # "68c02a44674e9",  # MMA
        # "68c02a4467a48",  # Racing
        "68c02a4464a38",  # Soccer
        # "68c02a4468cf7",  # Tennis
    ]
]


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    if not (event_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    soup_1 = HTMLParser(event_data.content)

    ifr_1 = soup_1.css_first("iframe#playerIframe")

    if not ifr_1 or not (src := ifr_1.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    parsed = urlparse(src)

    ifr_1_src = urljoin(
        BASE_URL,
        f"embed1/{parsed.path.split('/')[-1].split('_')[0]}.php",
    )

    if not (
        ifr_1_src_data := await network.request(
            ifr_1_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source. (IFR1)")
        return

    soup_2 = HTMLParser(ifr_1_src_data.content)

    ifr_2 = soup_2.css_first("center iframe")

    if not ifr_2 or not (ifr_2_src := ifr_2.attributes.get("src")):
        log.warning(f"URL {url_num}) Unable to locate iframe. (IFR2)")
        return

    ifr_2_src = f"https:{ifr_2_src}" if ifr_2_src.startswith("//") else ifr_2_src

    if not (ifr_2_src_data := await network.request(ifr_2_src, log=log)):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return

    valid_m3u8 = re.compile(r"src:\s+(\'|\")([^\']+)(\'|\")", re.I)

    if not (match := valid_m3u8.search(ifr_2_src_data.text)):
        log.warning(f"URL {url_num}) No source found.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match[2]


async def refresh_html_cache(
    date: str,
    sport_id: str,
    ts: float,
) -> dict[str, dict[str, str | float]]:

    events = {}

    if not (
        html_data := await network.request(
            urljoin(BASE_URL, f"events/{date}"),
            log=log,
            params={"sport_id": sport_id},
        )
    ):
        return events

    soup = HTMLParser(html_data.content)

    for section in soup.css(".events-section"):
        if not (sport_node := section.css_first(".section-titlte")):
            continue

        sport = sport_node.text(strip=True)

        for event in section.css(".section-event"):
            event_name = "Live Event"

            if teams := event.css_first(".event-competitors"):
                home, away = teams.text(strip=True).split("vs.")

                event_name = f"{away} vs {home}"

            if not (event_button := event.css_first(".event-button a")) or not (
                href := event_button.attributes.get("href")
            ):
                continue

            event_date = event.css_first(".event-countdown").attributes.get(
                "data-start"
            )

            event_dt = Time.from_str(event_date, timezone="UTC")

            key = f"[{sport}] {event_name} ({TAG})"

            events[key] = {
                "sport": sport,
                "event": event_name,
                "link": href,
                "event_ts": event_dt.timestamp(),
                "timestamp": ts,
            }

    return events


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    if not (events := HTML_CACHE.load()):
        log.info("Refreshing HTML cache")

        tasks = [
            refresh_html_cache(
                date,
                sport_id,
                now.timestamp(),
            )
            for date in [now.date(), now.delta(days=1).date()]
            for sport_id in SPORT_ENDPOINTS
        ]

        results = await asyncio.gather(*tasks)

        events = {k: v for data in results for k, v in data.items()}

        HTML_CACHE.write(events)

    live = []

    start_ts = now.delta(minutes=-30).timestamp()
    end_ts = now.delta(minutes=30).timestamp()

    for k, v in events.items():
        if k in cached_keys:
            continue

        if not start_ts <= v["event_ts"] <= end_ts:
            continue

        live.append(v)

    return live


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):

            handler = partial(
                process_event,
                url=(link := ev["link"]),
                url_num=i,
            )

            url = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.PW_S,
                log=log,
            )

            sport, event, ts = (
                ev["sport"],
                ev["event"],
                ev["event_ts"],
            )

            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": url,
                "logo": logo,
                "base": "https://hardsmart.click",
                "timestamp": ts,
                "id": tvg_id or "Live.Event.us",
                "link": link,
                "UA": "curl/8.19.0",
            }

            cached_urls[key] = entry

            if url:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
