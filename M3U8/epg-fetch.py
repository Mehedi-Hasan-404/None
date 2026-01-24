#!/usr/bin/env python3
import asyncio
import gzip
import re
from pathlib import Path
from xml.etree import ElementTree as ET

from scrapers.utils import get_logger, network

log = get_logger(__name__)

BASE_M3U8 = Path(__file__).parent / "base.m3u8"

EPG_FILE = Path(__file__).parent / "TV.xml"

EPG_URLS = [
    "https://epgshare01.online/epgshare01/epg_ripper_CA2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_DUMMY_CHANNELS.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_FANDUEL1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_UK1.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz",
    "https://epgshare01.online/epgshare01/epg_ripper_US_LOCALS1.xml.gz",
    "https://i.mjh.nz/Roku/all.xml.gz",
]

LIVE_IMG = "https://i.gyazo.com/978f2eb4a199ca5b56b447aded0cb9e3.png"

DUMMIES = {
    "Basketball.Dummy.us": LIVE_IMG,
    "Golf.Dummy.us": LIVE_IMG,
    "Live.Event.us": LIVE_IMG,
    "MLB.Baseball.Dummy.us": None,
    "NBA.Basketball.Dummy.us": None,
    "NFL.Dummy.us": None,
    "NHL.Hockey.Dummy.us": None,
    "PPV.EVENTS.Dummy.us": LIVE_IMG,
    "Racing.Dummy.us": LIVE_IMG,
    "Soccer.Dummy.us": LIVE_IMG,
    "Tennis.Dummy.us": LIVE_IMG,
    "WNBA.dummy.us": None,
}

REPLACE_IDs = {
    "NCAA Sports": {"old": "Sports.Dummy.us", "new": "NCAA.Sports.Dummy.us"},
    "UFC": {"old": "UFC.247.Dummy.us", "new": "UFC.Dummy.us"},
}


def get_tvg_ids() -> dict[str, str]:
    base_m3u8 = BASE_M3U8.read_text(encoding="utf-8").splitlines()

    tvg = {}

    for line in base_m3u8:
        if line.startswith("#EXTINF"):
            tvg_id = re.search(r'tvg-id="([^"]*)"', line)[1]
            tvg_logo = re.search(r'tvg-logo="([^"]*)"', line)[1]

            tvg[tvg_id] = tvg_logo

    return tvg


async def fetch_xml(url: str) -> ET.Element | None:
    if not (html_data := await network.request(url, log=log)):
        return

    try:
        decompressed_data = gzip.decompress(html_data.content)

        return ET.fromstring(decompressed_data)
    except Exception as e:
        log.error(f'Failed to decompress and parse XML from "{url}": {e}')

        return


def hijack_id(
    old: str,
    new: str,
    text: str,
    root: ET.Element,
) -> None:

    og_channel = root.find(f"./channel[@id='{old}']")

    if og_channel is not None:
        new_channel = ET.Element(og_channel.tag, {**og_channel.attrib, "id": new})

        display_name = og_channel.find("display-name")

        if display_name is not None:
            new_channel.append(ET.Element("display-name", display_name.attrib))
            new_channel[-1].text = text

        for child in og_channel:
            if child.tag == "display-name":
                continue

            new_child = ET.Element(child.tag, child.attrib)
            new_child.text = child.text

        root.remove(og_channel)

        root.append(new_channel)

    for program in root.findall(f"./programme[@channel='{old}']"):
        new_program = ET.Element(program.tag, {**program.attrib, "channel": new})

        for child in program:
            new_child = ET.Element(child.tag, child.attrib)
            new_child.text = child.text
            new_program.append(new_child)

        for tag_name in ["title", "desc", "sub-title"]:
            tag = new_program.find(tag_name)

            if tag is not None:
                tag.text = text

        root.remove(program)

        root.append(new_program)


async def main() -> None:
    log.info(f"{'=' * 10} Fetching EPG {'=' * 10}")

    tvg_ids = get_tvg_ids()

    tvg_ids |= DUMMIES | {v["old"]: LIVE_IMG for v in REPLACE_IDs.values()}

    root = ET.Element("tv")

    tasks = [fetch_xml(url) for url in EPG_URLS]

    results = await asyncio.gather(*tasks)

    for epg_data in results:
        if epg_data is None:
            continue

        for channel in epg_data.findall("channel"):
            if (channel_id := channel.get("id")) in tvg_ids:
                for icon_tag in channel.findall("icon"):
                    if logo := tvg_ids.get(channel_id):
                        icon_tag.set("src", logo)

                if (url_tag := channel.find("url")) is not None:
                    channel.remove(url_tag)

                root.append(channel)

        for program in epg_data.findall("programme"):
            if program.get("channel") in tvg_ids:
                title_text = program.find("title").text
                subtitle = program.find("sub-title")

                if (
                    title_text in ["NHL Hockey", "Live: NFL Football"]
                    and subtitle is not None
                ):
                    program.find("title").text = f"{title_text} {subtitle.text}"

                root.append(program)

        for k, v in REPLACE_IDs.items():
            hijack_id(**v, text=k, root=root)

    tree = ET.ElementTree(root)

    tree.write(EPG_FILE, encoding="utf-8", xml_declaration=True)

    log.info(f"EPG saved to {EPG_FILE.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())

    for hndlr in log.handlers:
        hndlr.flush()
        hndlr.stream.write("\n")

    try:
        asyncio.run(network.client.aclose())
    except Exception:
        pass
