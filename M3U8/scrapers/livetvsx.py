import re
from functools import partial
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "LTVSX"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = "https://livetv.sx"


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    r = await network.unvd_client.get(url)

    if r.status_code != 200:
        log.warning(f"{url_num}) Failed to get event data.")
        return nones

    soup_1 = HTMLParser(r.content)

    for a_elem in soup_1.css("a"):
        if not (src_title := a_elem.attributes.get("title")) or (
            "aliez" not in src_title.lower()
        ):
            continue

        href = a_elem.attributes["href"]

        event_url = href if href.startswith("http") else f"https:{href}"
        break

    else:
        log.warning(f"URL {url_num}) No valid sources found.")
        return nones

    if not (ev_data := await network.request(event_url, log=log)):
        log.warning(f"URL {url_num}) Failed to load url. (EVD2)")
        return nones

    soup_2 = HTMLParser(ev_data.content)

    ifr_1 = soup_2.css_first("tr > td > iframe")

    if not ifr_1 or not (ifr_1_src := ifr_1.attributes.get("src")):
        log.warning(f"URL {url_num}) No iframe element found.")
        return nones

    ifr_1_src = "".join(
        (ifr_1_src if ifr_1_src.startswith("http") else f"https:{ifr_1_src}").split()
    )

    if not (ev_data_3 := await network.request(ifr_1_src, log=log)):
        log.warning(f"URL {url_num}) Failed to load url. (EVD3)")
        return nones

    pattern = re.compile(r'pl\.init\((\'|\")([^"]*)(\'|\")\)', re.I)

    if not (match := pattern.search(ev_data_3.text)):
        log.warning(f"URL {url_num}) No M3U8 source found.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    m3u: str = match[2] if match[2].startswith("http") else f"https:{match[2]}"

    return m3u, ifr_1_src


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    events = []

    r = await network.unvd_client.get(
        urljoin(BASE_URL, "export/webmasters.php"),
        params={"lang": "en"},
    )

    if r.status_code != 200:
        log.warning("Failed to get php data.")
        return events

    soup = HTMLParser(r.content)

    if not (table := soup.css_first("table.tbl")):
        return events

    for row in table.css("tr > td"):
        if not (event_tbl := row.css_first("table")):
            continue

        sport_elem = event_tbl.css_first(".spr")
        league_elem = event_tbl.css_first(".cmp")
        link_elem = event_tbl.css_first("a.title")
        event_id_elem = row.css_first("div[id^='el']")

        if not (league_elem and sport_elem and link_elem and event_id_elem):
            continue

        elif not (event_id := event_id_elem.attributes.get("id")):
            continue

        sport = sport_elem.text(strip=True)
        league = league_elem.text(strip=True)
        event_name = link_elem.text(strip=True)

        if f"[{sport} - {league}] {event_name} ({TAG})" in cached_keys:
            continue

        events.append(
            {
                "sport": sport,
                "league": league,
                "event": event_name,
                "link": urljoin(BASE_URL, f"cache/links/en.{event_id[2:]}.html"),
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://livetv.sx/enx/"')

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

            sport, league, event = (
                ev["sport"],
                ev["league"],
                ev["event"],
            )

            key = f"[{sport} - {league}] {event} ({TAG})"

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
