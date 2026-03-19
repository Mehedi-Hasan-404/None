import json
import re
from functools import partial
from typing import Any

from playwright.async_api import Browser
from selectolax.parser import HTMLParser

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "STRMSGATE"

CACHE_FILE = Cache(TAG, exp=10_800)

BASE_URL = "https://streamingon.org/index.php"


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    events = []

    if not (
        html_data := await network.request(
            BASE_URL,
            params={
                "sport": "all",
                "league": "all",
                "sort": "time",
                "stream": "available",
                "day": "all",
            },
            log=log,
        )
    ):
        return events

    link_data_ptrn = re.compile(r"var\s+linkData\s+=\s+({.*?});", re.I | re.S)

    if not (match := link_data_ptrn.search(html_data.text)):
        log.warning("No `linkData` variable found.")
        return events

    link_data: dict[str, dict[str, Any]] = json.loads(match[1])

    start_dt = now.delta(minutes=-30)
    end_dt = now.delta(minutes=30)

    soup = HTMLParser(html_data.content)

    for body in soup.css(".sport-body"):
        if not (date_elem := body.css_first(".date-label")):
            continue

        event_date = date_elem.text(strip=True)

        for card in soup.css(".game-card"):
            if not (event_id := card.attributes.get("data-id")):
                continue

            if not (league_elem := card.css_first(".card-league")):
                continue

            if not (teams := card.css(".card-teams .card-team-name")):
                continue

            if not (time_elem := card.css_first(".card-time")):
                continue

            event_dt = Time.from_str(
                f"{event_date} {time_elem.text(strip=True)}",
                timezone="CET",
            )

            if not start_dt <= event_dt <= end_dt:
                continue

            sport = league_elem.text(strip=True)

            team_1, team_2 = (team.text(strip=True) for team in teams)

            event_name = f"{team_2} vs {team_1}"

            if f"[{sport}] {event_name} ({TAG})" in cached_keys:
                continue

            if not (event_info := link_data.get(event_id)):
                continue

            if not (stream_links := event_info.get("streamLinks")):
                continue

            if not (url := stream_links[0].get("url")):
                continue

            events.append(
                {
                    "sport": sport,
                    "event": event_name,
                    "link": url,
                    "timestamp": now.timestamp(),
                }
            )

    return events


async def scrape(browser: Browser) -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info(f'Scraping from "{BASE_URL}"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

        async with network.event_context(browser, stealth=False) as context:
            for i, ev in enumerate(events, start=1):
                async with network.event_page(context) as page:
                    handler = partial(
                        network.process_event,
                        url=(link := ev["link"]),
                        url_num=i,
                        page=page,
                        log=log,
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
                        ev["timestamp"],
                    )

                    key = f"[{sport}] {event} ({TAG})"

                    tvg_id, logo = leagues.get_tvg_info(sport, event)

                    entry = {
                        "url": url,
                        "logo": logo,
                        "base": "https://instreams.click/",
                        "timestamp": ts,
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
