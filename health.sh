#!/usr/bin/env bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
MAX_JOBS=10
BASE_FILE="./M3U8/base.m3u8"
README="./readme.md"

[[ ! -f "$BASE_FILE" ]] && exit 1

STATUSLOG=$(mktemp)

get_status() {
    local url="$1"
    local channel="$2"
    local index="$3"
    local total="$4"
    local attempt response status_code

    [[ "$url" != http* ]] && return

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
            -w "%{http_code}" \
            "$url" 2>&1
    )

    case "$response" in

    2* | 3*)
        printf '[%d/%d] ✔️  %s' "$((index + 1))" "$total" "$chnl_info"

        echo "PASS" >>"$STATUSLOG"
        ;;

    4* | 5*)
        printf '[%d/%d] ❌  %s' "$((index + 1))" "$total" "$chnl_info"

        echo "| $channel | HTTP Error ($response) | \`$url\` |" >>"$STATUSLOG"

        echo "FAIL" >>"$STATUSLOG"
        ;;

    *)
        printf '[%d/%d] ❌  %s' "$((index + 1))" "$total" "$chnl_info"

        if [[ "$response" == "000" ]]; then
            echo "| $channel | HTTP Timeout (408) | \`$url\` |" >>"$STATUSLOG"

        else
            echo "| $channel | Unknown status ($status_code) | \`$url\` |" >>"$STATUSLOG"

        fi

        echo "FAIL" >>"$STATUSLOG"
        ;;

    esac
}

check_links() {
    total_urls=$(grep -cE '^https?://' "$BASE_FILE")
    channel_num=0
    name=""

    printf "Checking %d links from %s\n" "$total_urls" "$BASE_FILE"

    echo "| Channel | Error (Code) | Link |" >"$STATUSLOG"
    echo "| ------- | ------------ | ---- |" >>"$STATUSLOG"

    while IFS= read -r line; do
        line=$(echo "$line" | tr -d '\r\n')

        if [[ "$line" == \#EXTINF* ]]; then
            name=$(echo "$line" | sed -n 's/.*tvg-name="\([^"]*\)".*/\1/p')

            [[ -z "$name" ]] && name="Channel $channel_num"

        elif [[ "$line" =~ ^https?:// ]]; then
            while (($(jobs -rp | wc -l) >= MAX_JOBS)); do sleep 0.2; done

            get_status "$line" "$name" "$channel_num" "$total_urls" &

            ((channel_num++))
        fi

    done < <(cat "$BASE_FILE")

    wait
    echo -e "\nDone."
}

write_readme() {
    local passed failed

    passed=$(grep -c '^PASS$' "$STATUSLOG")
    failed=$(grep -c '^FAIL$' "$STATUSLOG")

    {
        echo "## Base Log @ $(TZ="UTC" date "+%Y-%m-%d %H:%M %Z")"
        echo
        echo "### ✅ Working Streams: $passed<br>❌ Dead Streams: $failed"
        echo

        if (($failed > 0)); then
            head -n 1 "$STATUSLOG"
            grep -v -e '^PASS$' -e '^FAIL$' -e '^---' "$STATUSLOG" | grep -v '^| Channel' | sort -u
        fi

        echo "---"
        echo "#### Base Channels URL"
        echo -e "\`\`\`\nhttps://s.id/d9Base\n\`\`\`\n"
        echo "#### Live Events URL"
        echo -e "\`\`\`\nhttps://s.id/d9Live\n\`\`\`\n"
        echo "#### Combined (Base + Live Events) URL"
        echo -e "\`\`\`\nhttps://s.id/d9M3U8\n\`\`\`\n"
        echo "#### EPG URL"
        echo -e "\`\`\`\nhttps://s.id/d9sEPG\n\`\`\`\n"
        echo "---"
        echo "#### Mirrors"
        echo -n "[GitHub](https://github.com/doms9/iptv) | "
        echo -e "[GitLab](https://gitlab.com/doms9/iptv) | "
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
