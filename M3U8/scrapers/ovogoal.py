import re
from functools import partial

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "OVO"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://orbixa.top"


def fix_league(s: str) -> str:
    return " ".join(x.capitalize() for x in s.split()) if len(s) > 5 else s.upper()


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

    valid_m3u8 = re.compile(r'(var|const)\s+(\w+)\s*=\s*"([^"]*)"', re.I)

    if not (match := valid_m3u8.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No Clappr source found.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return match[3], iframe_src


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    sport = None

    for node in soup.css(".wrapper *"):
        if (cls := node.attributes.get("class")) == "section-title":
            sport = fix_league(node.text(strip=True))

        if node.tag == "a" and cls == "match":
            if not sport:
                continue

            if not (team_elems := node.css(".team")):
                continue

            if not (href := node.attributes.get("href")):
                continue

            event_name = " vs ".join(team.text(strip=True) for team in team_elems)

            if f"[{sport}] {event_name} ({TAG})" in cached_keys:
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
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

            url, iframe = await network.safe_process(
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
                "base": iframe,
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
