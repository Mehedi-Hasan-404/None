import re
from collections.abc import KeysView
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "OVO"

CACHE_FILE = Cache(TAG, exp=10_800)

HTML_FILE = Cache(f"{TAG}-html", exp=28_800)

BASE_URL = "https://ovogoalz.st"


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return nones

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return nones

    if not (
        iframe_src_data := await network.request(
            iframe_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return nones

    valid_m3u8 = re.compile(r'(var|const)\s+(\w+)\s+=\s+"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return match[3], iframe_src


async def refresh_html_cache(now: Time) -> dict[str, dict[str, str | float]]:
    events = {}

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    sport = "Live Event"

    for card in soup.css("a.match-card"):
        if not (href := card.attributes.get("href")) or len(href) == 1:
            continue

        if not (card_time_elem := card.css_first(".match-time")):
            continue

        if not (
            home_elem := card.css_first(
                ".match-team.home > .team-info > .team-full-name"
            )
        ):
            home_elem = card.css_first(".match-team.home > .team-full-name")

            event_name = home_elem.text(strip=True)

        else:
            away_elem = card.css_first(
                ".match-team.away > .team-info > .team-full-name"
            )

            home_team, away_team = home_elem.text(strip=True), away_elem.text(
                strip=True
            )

            event_name = f"{away_team} vs {home_team}"

        event_time = card_time_elem.text(strip=True)

        event_dt = Time.from_str(
            f"{' '.join(event_time.split()[:-1])} {now.year}",
            "%b %d — %H:%M %Y",
            timezone="CET",
        )

        key = f"[{sport}] {event_name} ({TAG})"

        events[key] = {
            "sport": sport,
            "name": event_name,
            "link": urljoin(f"{html_data.url}", href),
            "event_ts": event_dt.timestamp(),
            "timestamp": now.timestamp(),
        }

    return events


async def get_events(cached_keys: KeysView[str]) -> list[Event]:
    now = Time.clean(Time.now())

    if not (events := HTML_FILE.load()):
        log.info("Refreshing HTML cache")

        events = await refresh_html_cache(now)

        HTML_FILE.write(events)

    start_ts = now.delta(minutes=-30).timestamp()
    end_ts = now.delta(minutes=30).timestamp()

    breakpoint()

    return [
        Event(**v)
        for k, v in events.items()
        if k not in cached_keys and start_ts <= v.pop("event_ts") <= end_ts
    ]


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["m3u8"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            m3u8, iframe = await network.safe_process(
                handler,
                url_num=i,
                timeout_return=(None, None),
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "m3u8": m3u8,
                "logo": logo,
                "refer": iframe,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
                "link": ev.link,
            }

            cached_urls[key] = entry

            if m3u8:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
