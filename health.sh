#!/bin/bash
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36 Edg/134.0.0.0"
MAX_JOBS=10
base_file="./M3U8/base.m3u8"
README="./readme.md"
STATUSLOG=$(mktemp)

get_status() {
    local url="$1"
    local channel="$2"
    local attempt response status_code

    [[ "$url" != http* ]] && return

    output=$(
        timeout 10s ffprobe \
            -v error \
            -rw_timeout 15000000 \
            -timeout 15000000 \
            -select_streams v:0 \
            -show_entries stream=codec_name \
            -of csv=p=0 \
            -headers "User-Agent: $UA" \
            -analyzeduration 5M \
            -probesize 5M \
            -http_persistent 0 \
            "$url" 2>&1
    )

    rc=$?

    if ((rc == 0)); then
        printf '✔️  %s (%s)\n' "$channel" "$url"

        echo "PASS" >>"$STATUSLOG"
    else
        printf '❌ %s (%s)\n' "$channel" "$url"

        if [[ "$output" =~ Server\ returned\ ([0-9]{3})\ (.+) ]]; then
            code="${BASH_REMATCH[1]}"
            echo "| $channel | HTTP Error ($code) | \`$url\` |" >>"$STATUSLOG"
        elif ((rc == 124)); then
            echo "| $channel | HTTP Timeout (408) | \`$url\` |" >>"$STATUSLOG"
        else
            echo "| $channel | HTTP Error (000) | \`$url\` |" >>"$STATUSLOG"
        fi

        echo "FAIL" >>"$STATUSLOG"
    fi
}

check_links() {
    total_urls=$(grep -cE '^https?://' "$base_file")
    channel_num=0
    name=""

    echo -e "Checking $total_urls links from: $base_file\n"

    echo "| Channel | Error (Code) | Link |" >"$STATUSLOG"
    echo "| ------- | ------------ | ---- |" >>"$STATUSLOG"

    while IFS= read -r line; do
        line=$(echo "$line" | tr -d '\r\n')

        if [[ "$line" == \#EXTINF* ]]; then
            name=$(echo "$line" | sed -n 's/.*tvg-name="\([^"]*\)".*/\1/p')
            [[ -z "$name" ]] && name="Channel $channel_num"

        elif [[ "$line" =~ ^https?:// ]]; then
            while (($(jobs -rp | wc -l) >= MAX_JOBS)); do sleep 0.2; done
            get_status "$line" "$name" &
            ((channel_num++))
        fi

    done < <(cat "$base_file")

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
