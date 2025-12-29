import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "TOTALSPRTK"

CACHE_FILE = Cache(f"{TAG.lower()}.json", exp=28_800)

BASE_URL = "https://live.totalsportek777.com/"


def fix_league(s: str) -> str:
    return s.upper() if s.islower() else s


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    if not (html_data := await network.request(url, log=log)):
        log.info(f"URL {url_num}) Failed to load url.")

        return None, None

    soup = HTMLParser(html_data.content)

    if not (iframe := soup.css_first("iframe")):
        log.warning(f"URL {url_num}) No iframe element found.")

        return None, None

    if (
        not (iframe_src := iframe.attributes.get("src"))
        or "xsportportal" not in iframe_src
    ):
        log.warning(f"URL {url_num}) No valid iframe source found.")

        return None, None

    if not (iframe_src_data := await network.request(iframe_src, log=log)):
        log.info(f"URL {url_num}) Failed to load iframe source.")

        return None, None

    valid_m3u8 = re.compile(r'var\s+(\w+)\s*=\s*"([^"]*)"', re.IGNORECASE)

    if not (match := valid_m3u8.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")

        return None, None

    log.info(f"URL {url_num}) Captured M3U8")

    return bytes.fromhex(match[2]).decode("utf-8"), iframe_src


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    sport = "Live Event"

    for box in soup.css(".div-main-box"):
        for node in box.iter():
            if not (node_class := node.attributes.get("class")):
                continue

            if "my-1" in node_class:
                if span := node.css_first("span"):
                    sport = span.text(strip=True)

            if node.tag == "a" and "nav-link2" in node_class:
                if not (time_node := node.css_first(".col-3")):
                    continue

                if time_node.text(strip=True) != "MatchStarted":
                    continue

                if not (href := node.attributes.get("href")) or href.startswith("http"):
                    continue

                sport = fix_league(sport)

                teams = [t.text(strip=True) for t in node.css(".col-7 .col-12")]

                event_name = " vs ".join(teams)

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

    events = await get_events(cached_urls.keys())

    log.info(f"Processing {len(events)} new URL(s)")

    if events:
        now = Time.clean(Time.now())

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev["link"],
                url_num=i,
            )

            url, iframe = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

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
                "base": iframe,
                "timestamp": now.timestamp(),
                "id": tvg_id or "Live.Event.us",
                "link": link,
            }

            cached_urls[key] = entry

            if url:
                valid_count += 1

                urls[key] = entry

    if new_count := valid_count - cached_count:
        log.info(f"Collected and cached {new_count} new event(s)")

    else:
        log.info("No new events found")

    CACHE_FILE.write(cached_urls)
