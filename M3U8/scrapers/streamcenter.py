import re
from functools import partial
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser

from .utils import Cache, Event, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMCNTR"

CACHE_FILE = Cache(TAG, exp=28_800)

BASE_URL = "https://streamecenter.live"

ALT_BASE = "https://streame.center"


def cleanup(s: str) -> str:
    return "".join(i for i in s.split("—")[-1] if i.isascii()).strip()


async def process_event(url: str, url_num: int) -> str | None:
    if not (html_data := await network.request(url, url_num, log=log)):
        return

    soup = HTMLParser(html_data.content)

    iframe = soup.css_first("iframe")

    if not iframe or not (src := iframe.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return

    if not (
        iframe_src_data := await network.request(
            network.ensure_https(src),
            url_num,
            headers={"Referer": ALT_BASE},
            log=log,
        )
    ):
        return

    pattern = re.compile(r'input:\s+"([^"]*)"', re.I)

    if not (match := pattern.search(iframe_src_data.text)):
        log.warning(f"URL {url_num}) No encrypted URL found.")
        return

    decrypted_data = await network.client.post(
        urljoin(ALT_BASE, "embed/decrypt.php"),
        data={"input": match[1]},
    )

    if not decrypted_data.is_success:
        log.warning(f"URL {url_num}) Failed to decrypt URL.")
        return

    log.info(f"URL {url_num}) Captured M3U8")

    return decrypted_data.text.split("?")[0]


async def get_events() -> list[Event]:
    events: list[Event] = []

    if not (
        html_data := await network.request(
            urljoin(BASE_URL, "game-cards/embed"),
            log=log,
        )
    ):
        return events

    soup = HTMLParser(html_data.content)

    now = Time.clean(Time.now())

    for card in soup.css(".game-card-group"):
        if not (sport_elem := card.css_first("h2")):
            continue

        for game in card.css(".game-card-row"):
            if not (name_elem := game.css_first("h3")):
                continue

            if not (event_time_elem := game.css_first(".game-card-when > time")):
                continue

            elif not (event_time := event_time_elem.attributes.get("datetime")):
                continue

            event_dt = Time.fromisoformat(event_time).to_tz("EST")

            if event_dt.date() != now.date():
                continue

            sport = cleanup(sport_elem.text(strip=True))

            event_name = name_elem.text(strip=True)

            for source in game.css(".game-card-source > a.game-card-open-link"):
                if not (href := source.attributes.get("href")):
                    continue

                lang = source.text(strip=True) or "English"

                events.append(
                    Event(
                        sport=sport,
                        name=f"{event_name} | {lang}",
                        link=urljoin(BASE_URL, href),
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
