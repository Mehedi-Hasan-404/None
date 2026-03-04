import asyncio
import re
from functools import partial

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "WEBCAST"

CACHE_FILE = Cache(TAG, exp=19_800)

BASE_URLS = {
    "MLB": "https://mlbwebcast.com",
    # "NFL": "https://nflwebcast.com",
    "NHL": "https://slapstreams.com",
}


def fix_event(s: str) -> str:
    return " vs ".join(s.split("@"))


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")

        return

    soup = HTMLParser(event_data.content)

    if not (iframe := soup.css_first('iframe[name="srcFrame"]')):
        log.warning(f"URL {url_num}) No iframe element found.")

        return

    if not (iframe_src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found.")

        return

    if not (
        iframe_src_data := await network.request(
            iframe_src,
            headers={"Referer": url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source.")

        return

    pattern = re.compile(r"source:\s+(\'|\")(.*)(\'|\")", re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")

        return

    log.info(f"URL {url_num}) Captured M3U8")

    return match[2]


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    tasks = [network.request(url, log=log) for url in BASE_URLS.values()]

    results = await asyncio.gather(*tasks)

    events = []

    if not (
        soups := [(HTMLParser(html.content), html.url) for html in results if html]
    ):
        return events

    for soup, url in soups:
        sport = next((k for k, v in BASE_URLS.items() if v == url), "Live Event")

        for row in soup.css("tr.singele_match_date"):
            if not (vs_node := row.css_first("td.teamvs a")):
                continue

            event_name = vs_node.text(strip=True)

            for span in vs_node.css("span.mtdate"):
                date = span.text(strip=True)

                event_name = event_name.replace(date, "").strip()

            if not (href := vs_node.attributes.get("href")):
                continue

            event = fix_event(event_name)

            if f"[{sport}] {event} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event,
                    "link": href,
                }
            )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{' & '.join(BASE_URLS.values())}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

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
                semaphore=network.PW_S,
                log=log,
            )

            sport, event = ev["sport"], ev["event"]

            key = f"[{sport}] {event} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(sport, event)

            entry = {
                "url": url,
                "logo": logo,
                "base": BASE_URLS[sport],
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "link": link,
            }

            cached_urls[key] = entry

            if url:
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
