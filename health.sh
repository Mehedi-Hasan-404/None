#!/usr/bin/env bash

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
MAX_JOBS=10
BASE_FILE="./M3U8/base.m3u8"
README="./readme.md"

[[ ! -f $BASE_FILE ]] && echo "$BASE_FILE does not exist" && exit 1

shopt -s nocasematch

STATUSLOG=$(mktemp)

get_status() {
    local url="$1"
    local channel="$2"
    local index="$3"
    local total="$4"
    local chnl_info response rc IFS status_code content_type

    [[ $url != http* ]] && return

    printf -v chnl_info "%s (%s)\n" "$channel" "$url"

    response=$(
        curl -skL \
            -A "$UA" \
            -H "Accept: */*" \
            -H "Accept-Language: en-US,en;q=0.9" \
            -H "Connection: keep-alive" \
            -o /dev/null \
            --compressed \
            --max-time 10 \
            -w "%{http_code}|%{content_type}" \
            "$url" 2>&1
    )

    rc=$?

    IFS="|" read -r status_code content_type <<<"$response"

    if ((rc != 0)); then
        if [[ $status_code == 2* && $rc == 28 ]]; then
            printf "[%d/%d] ✔️  %s" "$((index + 1))" "$total" "$chnl_info"

            echo "PASS" >>"$STATUSLOG"

        else
            printf "[%d/%d] ❌  %s" "$((index + 1))" "$total" "$chnl_info"

            printf "%s\t%s\tcURL Error (%s)\n" \
                "$url" "$channel" "$rc" >>"$STATUSLOG"

            echo "FAIL" >>"$STATUSLOG"
        fi

    elif [[ $status_code != 2* ]]; then
        printf "[%d/%d] ❌  %s" "$((index + 1))" "$total" "$chnl_info"

        printf "%s\t%s\tHTTP Error (%s)\n" \
            "$url" "$channel" "$status_code" >>"$STATUSLOG"

        echo "FAIL" >>"$STATUSLOG"

    else
        case "$content_type" in

        application/vnd.apple.mpegurl* | \
            application/x-mpegURL* | \
            application/octet-stream* | \
            video/mpeg* | \
            video/mp2t* | \
            text/plain*)

            printf "[%d/%d] ✔️  %s" "$((index + 1))" "$total" "$chnl_info"

            echo "PASS" >>"$STATUSLOG"
            ;;

        text/html* | *)

            printf "[%d/%d] ❌  %s" "$((index + 1))" "$total" "$chnl_info"

            printf "%s\t%s\tInvalid M3U8 (%s)\n" \
                "$url" "$channel" "$status_code" >>"$STATUSLOG"

            echo "FAIL" >>"$STATUSLOG"
            ;;
        esac
    fi
}

check_links() {

    # shellcheck disable=SC2155
    local total_urls=$(grep -cE '^https?://' "$BASE_FILE")
    local channel_num=0
    local name=""
    local IFS line

    printf "Checking %d links from %s\n\n" "$total_urls" "$BASE_FILE"

    while IFS= read -r line; do
        line=${line//$'\r'/}

        if [[ $line == \#EXTINF* ]]; then
            name=$(sed -n 's/.*tvg-name="\([^"]*\)".*/\1/p' <<<"$line")

            [[ -z $name ]] && name="Channel $((channel_num + 1))"

        elif [[ $line =~ ^https?:// ]]; then
            while (($(jobs -rp | wc -l) >= MAX_JOBS)); do wait -n; done

            get_status "$line" "$name" "$channel_num" "$total_urls" &

            ((channel_num++))
        fi

    done <"$BASE_FILE"

    wait
    echo -e "\nDone."
}

write_readme() {
    local base="https://s.id/d9M3U8"
    local live="https://s.id/d9Live"
    local combined="https://s.id/d9M3U8"
    local epg="https://s.id/d9sEPG"

    local commits="https://github.com/doms9/iptv/commits/default"

    local datefmt="%Y-%m-%d %H:%M %Z"
    local TZ IFS url channel error

    # shellcheck disable=SC2155
    local passed=$(grep -c '^PASS$' "$STATUSLOG")

    # shellcheck disable=SC2155
    local failed=$(grep -c '^FAIL$' "$STATUSLOG")

    {
        echo -e "<h1 align='center'>\U1F4FA IPTV</h1>"
        echo "<p align='center'>"
        printf "<a href='%s'><img src='%s'></a>\n" "$base" "https://img.shields.io/badge/updates-hourly-a396ff"
        printf "<a href='%s'><img src='%s'></a>\n" "$commits" "https://img.shields.io/github/commit-activity/w/doms9/iptv"
        printf "<img src='%s'>\n" "https://img.shields.io/github/license/doms9/iptv"
        printf "<img src='%s'>\n" "https://img.shields.io/badge/Python-4584b6?logo=python&logoColor=fff"
        echo "</p><br>"
        echo
        TZ="UTC" printf "<h2 align='center'>Base Log @ %($datefmt)T</h2>\n" -1
        echo
        printf "<h3 align='center'>✅ Working Streams: %d<br>❌ Dead Streams: %d</h3>\n" \
            "$passed" "$failed"

        echo

        if ((failed > 0)); then
            echo "<table align='center'>"
            echo "<thead>"
            echo "<tr><th align='center'>Channel</th><th align='center'>Error (Code)</th></tr>"
            echo "</thead>"
            echo "<tbody>"

            sort -V -t $'\t' -k 2,2 -u -f "$STATUSLOG" |
                while IFS=$'\t' read -r url channel error; do
                    if [[ ! $url =~ ^(PASS|FAIL)$ ]]; then
                        printf "<tr><td><a href='%s'>%s</a></td><td>%s</td></tr>\n" \
                            "$url" "$channel" "$error"
                    fi
                done

            echo "</tbody>"
            echo "</table>"
        fi

        echo -e "\n---"
        echo "#### Base Channels"
        echo -e "\`\`\`\n$base\n\`\`\`\n"
        echo "#### Live Events"
        echo -e "\`\`\`\n$live\n\`\`\`\n"
        echo "#### Combined (Base Channels + Live Events)"
        echo -e "\`\`\`\n$combined\n\`\`\`\n"
        echo "#### EPG"
        echo -e "\`\`\`\n$epg\n\`\`\`\n"
        echo "---"
        echo "#### Mirrors"
        echo -n "[GitHub](https://github.com/doms9/iptv) | "
        echo -e "[GitLab](https://gitlab.com/doms9/iptv) |"
        echo -e "[Forgejo](https://forgejo.mxnticek.eu/doms/iptv)\n"
        echo "---"
        echo "#### Legal Disclaimer"
        echo "This repository lists publicly accessible IPTV streams as found on the internet at the time of checking."
        echo "No video or audio content is hosted in this repository. These links may point to copyrighted material owned by third parties;"
        echo "they are provided **solely for educational and research purposes.**"
        echo "The author does not endorse, promote, or encourage illegal streaming or copyright infringement."
        echo "End users are solely responsible for ensuring they comply with all applicable laws in their jurisdiction before using any link in this repository."
        echo "If you are a rights holder and wish for a link to be removed, please open an issue."

    } >"$README"
}

check_links
write_readme
rm "$STATUSLOG"
