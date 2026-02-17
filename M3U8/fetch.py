#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright
from scrapers import (
    cdnlivetv,
    embedhd,
    fawa,
    istreameast,
    livetvsx,
    ovogoal,
    pawa,
    pixel,
    ppv,
    roxie,
    shark,
    sport9,
    streambtw,
    streamcenter,
    streamhub,
    streamsgate,
    totalsportek,
    tvapp,
    watchfooty,
    webcast,
    xstreameast,
)
from scrapers.utils import get_logger, network

log = get_logger(__name__)

BASE_FILE = Path(__file__).parent / "base.m3u8"

EVENTS_FILE = Path(__file__).parent / "events.m3u8"

COMBINED_FILE = Path(__file__).parent / "TV.m3u8"


def load_base() -> tuple[list[str], int]:
    log.info("Fetching base M3U8")

    data = BASE_FILE.read_text(encoding="utf-8")

    pattern = re.compile(r'tvg-chno="(\d+)"')

    last_chnl_num = max(map(int, pattern.findall(data)), default=0)

    return data.splitlines(), last_chnl_num


async def main() -> None:
    log.info(f"{'=' * 10} Scraper Started {'=' * 10}")

    base_m3u8, tvg_chno = load_base()

    async with async_playwright() as p:
        try:
            hdl_brwsr = await network.browser(p)

            xtrnl_brwsr = await network.browser(p, external=True)

            pw_tasks = [
                asyncio.create_task(cdnlivetv.scrape(hdl_brwsr)),
                asyncio.create_task(embedhd.scrape(hdl_brwsr)),
                asyncio.create_task(pixel.scrape(hdl_brwsr)),
                asyncio.create_task(ppv.scrape(xtrnl_brwsr)),
                asyncio.create_task(roxie.scrape(hdl_brwsr)),
                asyncio.create_task(sport9.scrape(xtrnl_brwsr)),
                asyncio.create_task(streamcenter.scrape(xtrnl_brwsr)),
                # asyncio.create_task(streamhub.scrape(xtrnl_brwsr)),
                asyncio.create_task(streamsgate.scrape(xtrnl_brwsr)),
                asyncio.create_task(totalsportek.scrape(hdl_brwsr)),
                # asyncio.create_task(tvapp.scrape(hdl_brwsr)),
                asyncio.create_task(webcast.scrape(hdl_brwsr)),
            ]

            httpx_tasks = [
                asyncio.create_task(fawa.scrape()),
                asyncio.create_task(istreameast.scrape()),
                asyncio.create_task(ovogoal.scrape()),
                asyncio.create_task(pawa.scrape()),
                asyncio.create_task(shark.scrape()),
                asyncio.create_task(streambtw.scrape()),
                asyncio.create_task(xstreameast.scrape()),
            ]

            await asyncio.gather(*(pw_tasks + httpx_tasks))

            # others
            await watchfooty.scrape(xtrnl_brwsr)
            await livetvsx.scrape(xtrnl_brwsr)

        finally:
            await hdl_brwsr.close()

            await xtrnl_brwsr.close()

            await network.client.aclose()

    additions = (
        cdnlivetv.urls
        | embedhd.urls
        | fawa.urls
        | istreameast.urls
        | livetvsx.urls
        | ovogoal.urls
        | pawa.urls
        | pixel.urls
        | ppv.urls
        | roxie.urls
        | shark.urls
        | sport9.urls
        | streambtw.urls
        | streamcenter.urls
        | streamhub.urls
        | streamsgate.urls
        | totalsportek.urls
        | tvapp.urls
        | watchfooty.urls
        | webcast.urls
        | xstreameast.urls
    )

    live_events: list[str] = []

    combined_channels: list[str] = []

    for i, (event, info) in enumerate(
        sorted(additions.items()),
        start=1,
    ):
        extinf_all = (
            f'#EXTINF:-1 tvg-chno="{tvg_chno + i}" tvg-id="{info["id"]}" '
            f'tvg-name="{event}" tvg-logo="{info["logo"]}" group-title="Live Events",{event}'
        )

        extinf_live = (
            f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{info["id"]}" '
            f'tvg-name="{event}" tvg-logo="{info["logo"]}" group-title="Live Events",{event}'
        )

        vlc_block = [
            f'#EXTVLCOPT:http-referrer={info["base"]}',
            f'#EXTVLCOPT:http-origin={info["base"]}',
            f"#EXTVLCOPT:http-user-agent={network.UA}",
            info["url"],
        ]

        combined_channels.extend(["\n" + extinf_all, *vlc_block])

        live_events.extend(["\n" + extinf_live, *vlc_block])

    COMBINED_FILE.write_text(
        "\n".join(base_m3u8 + combined_channels),
        encoding="utf-8",
    )

    log.info(f"Base + Events saved to {COMBINED_FILE.resolve()}")

    EVENTS_FILE.write_text(
        '#EXTM3U url-tvg="https://raw.githubusercontent.com/doms9/iptv/refs/heads/default/M3U8/TV.xml"\n'
        + "\n".join(live_events),
        encoding="utf-8",
    )

    log.info(f"Events saved to {EVENTS_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

    for hndlr in log.handlers:
        hndlr.flush()
        hndlr.stream.write("\n")
