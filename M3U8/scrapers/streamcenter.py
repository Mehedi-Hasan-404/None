import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://streame.center"


def cleanup(s: str) -> str:
    return "".join(i for i in s.split("—")[0] if i.isascii()).strip()


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url.")
        return

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    if not (
        iframe_src_data := await network.request(
            network.ensure_https(src),
            headers={"Referer": url},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load iframe source.")
        return

    pattern = re.compile(r'input:\s+"([^"]*)"', re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No encrypted URL found.")
        return

    if not (
        decrypted := await network.client.post(
            urljoin(BASE_URL, "embed/decrypt.php"),
            data={"input": match[1]},
        )
    ):
        log.warning(f"URL {url_num}) Failed to decrypt URL.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return decrypted.text.split("?")[0]


async def get_events() -> list[Event]:
    events: list[Event] = []

    if not (html_data := await network.request(BASE_URL, log=log)):
        return events

    soup = HTMLParser(html_data.content)

    now = Time.clean(Time.now())

    if not (date_elem := soup.css_first(".tg-header > p")):
        return events

    elif now.strftime("%A, %B %d, %Y") != date_elem.text(strip=True):
        return events

    for info in soup.css(".tg-cat"):
        if not (sport_elem := info.css_first("h2")):
            continue

        sport = cleanup(sport_elem.text())

        for game in info.css(".tg-game"):
            if not (event_name_elem := game.css_first(".tg-title")):
                continue

            event_name = cleanup(event_name_elem.text())

            for link in game.css(".tg-lang"):
                if not (event_lang_elem := link.css_first(".tg-watch")):
                    continue

                elif not (a_elem := link.css_first("a")) or not (
                    href := a_elem.attributes.get("href")
                ):
                    continue

                event_lang = cleanup(event_lang_elem.text())

                events.append(
                    Event(
                        sport=sport,
                        name=(
                            f"{event_name} | {event_lang}" if event_name else event_lang
                        ),
                        link=href,
                        timestamp=now.timestamp(),
                    )
                )

    return events


async def scrape() -> None:
    if cached_urls := CACHE_FILE.load():
        urls.update({k: v for k, v in cached_urls.items() if v["source"]})

        log.info(f"Loaded {len(urls)} event(s) from cache")

        return

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events():
        log.info(f"Processing {len(events)} URL(s)")

        for i, ev in enumerate(events, start=1):
            handler = partial(
                process_event,
                url=ev.link,
                url_num=i,
            )

            source = await network.safe_process(
                handler,
                url_num=i,
                semaphore=network.HTTP_S,
                log=log,
            )

            key = f"[{ev.sport}] {ev.name} ({TAG})"

            tvg_id, logo = leagues.get_tvg_info(ev.sport, ev.name)

            entry = {
                "source": source,
                "logo": logo,
                "refer": BASE_URL,
                "timestamp": ev.timestamp,
                "tvg-id": tvg_id or "Live.Event.us",
                "link": ev.link,
            }

            cached_urls[key] = entry

            if source:
                urls[key] = entry

        log.info(f"Collected and cached {len(urls)} event(s)")

    else:
        log.info("No events found")

    CACHE_FILE.write(cached_urls)
