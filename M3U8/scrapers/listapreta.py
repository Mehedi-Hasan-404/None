from functools import partial

from .utils import Cache, Time, get_logger, leagues, network

log = get_logger(__name__)

urls: dict[str, dict[str, str | float]] = {}

TAG = "LISTA"

CACHE_FILE = Cache(TAG, exp=28_800)

API_URL = "https://listapreta.site/sports-widget/events.php"


async def process_event(url: str, url_num: int) -> tuple[str | None, str | None]:
    nones = None, None

    event_id = url.split("id=")[-1]

    if not (
        token_req := await network.request(
            "https://lista-preta-tv.site/generate_token.php",
            params={"id": event_id},
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Failed to load token data.")
        return nones

    if not (token_data := token_req.json()):
        log.warning(f"URL {url_num}) No token data available.")
        return nones

    elif not (token := token_data.get("token")) or not (exp := token_data.get("exp")):
        log.warning(f"URL {url_num}) No token data available.")
        return nones

    ref = f"https://lista-preta-tv.site/player-all.html?id={event_id}"

    if not (
        m3u8_req := await network.request(
            "https://lista-preta-tv.site/m3u8.php",
            headers={"Referer": ref},
            params={"id": event_id, "token": token, "exp": exp},
            follow_redirects=False,
            log=log,
        )
    ):
        log.warning(f"URL {url_num}) Unable to fetch M3U8 request.")
        return nones

    elif not (m3u8 := m3u8_req.headers.get("Location")):
        log.warning(f"URL {url_num}) Unable to fetch M3U8 request.")
        return nones

    log.info(f"URL {url_num}) Captured M3U8")

    return m3u8, ref


async def get_events(cached_keys: list[str]) -> list[dict[str, str]]:
    now = Time.clean(Time.now())

    events = []

    if not (api_req := await network.request(API_URL, log=log)):
        return events

    elif not (api_data := api_req.json()) or api_data.get("error"):
        return events

    for event in api_data:
        sport = event.get("sport")

        t1, t2 = event.get("home"), event.get("away")

        if not (sport and t1 and t2):
            continue

        event_name = f"{t1} vs {t2}"

        if f"[{sport}] {event_name} ({TAG})" in cached_keys:
            continue

        event_dt = Time.from_str(event["start"], timezone="UTC")

        if now.date() != event_dt.date():
            continue

        if not (channels := event.get("channels")):
            continue

        event_links: list[str] = [channel["url"] for channel in channels]

        link = event_links[0]

        events.append(
            {
                "sport": sport,
                "event": event_name,
                "link": link,
                "timestamp": now.timestamp(),
            }
        )

    return events


async def scrape() -> None:
    cached_urls = CACHE_FILE.load()

    valid_urls = {k: v for k, v in cached_urls.items() if v["url"]}

    valid_count = cached_count = len(valid_urls)

    urls.update(valid_urls)

    log.info(f"Loaded {cached_count} event(s) from cache")

    log.info('Scraping from "https://listapreta.site"')

    if events := await get_events(cached_urls.keys()):
        log.info(f"Processing {len(events)} new URL(s)")

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
                "base": iframe,
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
