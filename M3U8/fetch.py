#!/usr/bin/env python3
import asyncio
import re
from pathlib import Path

from playwright.async_api import async_playwright
from scrapers import (
    cdnlivetv,
    dami,
    embedhd,
    embedsport,
    fawa,
    flyembed,
    istreameast,
    mainportal,
    pelotalibre,
    playfast,
    sportspass,
    streamcenter,
    streamfree,
    streamgate,
    streamtp,
    streamxhd,
    watchfooty,
    webcast,
    xyz2,
    xyzstreams,
)
from scrapers.utils import get_logger, network

log = get_logger(Path(__file__).stem)

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
                asyncio.create_task(cdnlivetv.scrape(xtrnl_brwsr)),
                asyncio.create_task(embedhd.scrape(hdl_brwsr)),
                asyncio.create_task(playfast.scrape(hdl_brwsr)),
                asyncio.create_task(sportspass.scrape(xtrnl_brwsr)),
                asyncio.create_task(watchfooty.scrape(xtrnl_brwsr)),
            ]

            httpx_tasks = [
                asyncio.create_task(dami.scrape()),
                asyncio.create_task(embedsport.scrape()),
                asyncio.create_task(fawa.scrape()),
                asyncio.create_task(flyembed.scrape()),
                asyncio.create_task(istreameast.scrape()),
                asyncio.create_task(mainportal.scrape()),
                asyncio.create_task(pelotalibre.scrape()),
                asyncio.create_task(streamcenter.scrape()),
                asyncio.create_task(streamfree.scrape()),
                asyncio.create_task(streamgate.scrape()),
                asyncio.create_task(streamtp.scrape()),
                asyncio.create_task(streamxhd.scrape()),
                asyncio.create_task(webcast.scrape()),
                # asyncio.create_task(xyz2.scrape()),
                asyncio.create_task(xyzstreams.scrape()),
            ]

            await asyncio.gather(*(pw_tasks + httpx_tasks))

        finally:
            await hdl_brwsr.close()

            await xtrnl_brwsr.close()

            await network.client.aclose()

    additions = (
        cdnlivetv.urls
        | dami.urls
        | embedhd.urls
        | embedsport.urls
        | fawa.urls
        | flyembed.urls
        | istreameast.urls
        | mainportal.urls
        | pelotalibre.urls
        | playfast.urls
        | sportspass.urls
        | streamcenter.urls
        | streamfree.urls
        | streamgate.urls
        | streamtp.urls
        | streamxhd.urls
        | watchfooty.urls
        | webcast.urls
        | xyz2.urls
        | xyzstreams.urls
    )

    live_events: list[str] = []

    combined_channels: list[str] = []

    for i, (event_name, event_info) in enumerate(
        sorted(additions.items()),
        start=1,
    ):
        tvg_id, logo, refer, source = (
            event_info[x]
            for x in (
                "tvg-id",
                "logo",
                "refer",
                "source",
            )
        )

        ua: str = event_info.get("user-agent", network.UA)

        extinf_all = (
            f'#EXTINF:-1 tvg-chno="{tvg_chno + i}" tvg-id="{tvg_id}" '
            f'tvg-name="{event_name}" tvg-logo="{logo}" group-title="Live Events",{event_name}'
        )

        extinf_live = (
            f'#EXTINF:-1 tvg-chno="{i}" tvg-id="{tvg_id}" '
            f'tvg-name="{event_name}" tvg-logo="{logo}" group-title="Live Events",{event_name}'
        )

        vlc_block: list[str] = [
            f"#EXTVLCOPT:http-referrer={refer}",
            f"#EXTVLCOPT:http-origin={refer}",
            f"#EXTVLCOPT:http-user-agent={ua}",
            source,
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
