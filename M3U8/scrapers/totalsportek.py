import json
import re
from functools import partial
from urllib.parse import urljoin, urlparse

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TOTALSPRTK"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://live3.totalsportek.foo"


def fix_txt(s: str) -> str:
    s = " ".join(s.split())

    return s.upper() if s.islower() else s


async def process_event(url: str, url_num: int) -> str | None:
    if not (event_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")

        return

    soup_1 = HTMLParser(event_data.content)

    if not (iframe_1 := soup_1.css_first("iframe")):
        log.warning(f"URL {url_num}) No iframe element found. (IFR1)")

        return

    if not (iframe_1_src := iframe_1.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found. (IFR1)")

        return

    if not (iframe_1_src_data := await network.request(iframe_1_src, log=log)):
        log.warning(f"URL {url_num}) Failed to load iframe source. (IFR1)")

        return

    soup_2 = HTMLParser(iframe_1_src_data.content)

    if not (iframe_2 := soup_2.css_first("iframe")):
        log.warning(f"URL {url_num}) No iframe element found. (IFR2)")

        return

    if not (iframe_2_src := iframe_2.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe source found. (IFR2)")

        return

    if not (
        iframe_2_src_data := await network.request(
            iframe_2_src,
            log=log,
            headers={"Referer": iframe_1_src},
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source. (IFR2)")

        return

    valid_m3u8 = re.compile(r'currentStreamUrl\s+=\s+"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(iframe_2_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found. (IFR2)")

        return

    log.info(f"URL {url_num}) Captured M3U8")

    return json.loads(f'"{match[1]}"')


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    sport = "Live Event"

    for node in soup.css("a"):
        if not node.attributes.get("class"):
            continue

        if (parent := node.parent) and "my-1" in parent.attributes.get("class", ""):
            if span := node.css_first("span"):
                sport = span.text(strip=True)

        sport = fix_txt(sport)

        if not (teams := [t.text(strip=True) for t in node.css(".col-7 .col-12")]):
            continue

        if not (href := node.attributes.get("href")):
            continue

        href = urlparse(href).path if href.startswith("http") else href

        if not (time_node := node.css_first(".col-3 span")):
            continue

        if time_node.text(strip=True).lower() != "matchstarted":
            continue

        event_name = fix_txt(" vs ".join(teams))

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": urljoin(BASE_URL, href),
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

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
                valid_count += 1

                urls[key] = entry

        log.info(f"Collected and cached {valid_count - cached_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
