"""Post new Nintendo Switch / Switch 2 games in the PIKI libraries to Discord.

Fetches every Switch and Switch 2 record held by PIKI (Pirkanmaa) libraries
from the Finna API, compares the IDs against seen_games.json, and posts any
unseen ones to a Discord webhook (DISCORD_WEBHOOK env var).

    python check.py            # normal run
    python check.py --dry-run  # print what would be posted, change nothing
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://api.finna.fi/v1/search"
RECORD_URL = "https://piki.finna.fi/Record/{}"
SEARCH_URL = ("https://piki.finna.fi/Search/Results?filter%5B%5D=~format%3A%222%2FGame%2FVideoGame%2FSwitch%2F%22"
              "&filter%5B%5D=~format%3A%222%2FGame%2FVideoGame%2FSwitch2%2F%22&sort=first_indexed+desc")
COVER_BASE = "https://finna.fi"
USER_AGENT = "KirjastoAuto (+https://github.com/Juuzoz/KirjastoAuto)"
STATE_FILE = Path(__file__).with_name("seen_games.json")

PLATFORMS = {
    "2/Game/VideoGame/Switch/": "Nintendo Switch",
    "2/Game/VideoGame/Switch2/": "Nintendo Switch 2",
}
PAGE_SIZE = 100
# More new games than this in one run most likely means Finna re-indexed
# records under new IDs, so post one summary instead of flooding the channel.
FLOOD_LIMIT = 30
EMBEDS_PER_MESSAGE = 10  # Discord's maximum
SWITCH_RED = 0xE60012

TITLE_SUFFIX = re.compile(r"\s*:\s*Nintendo Switch(\s*2)?\.?\s*$", re.IGNORECASE)


def http_json(url, data=None):
    headers = {"User-Agent": USER_AGENT}
    if data is not None:
        data = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return json.loads(body) if body else None


def fetch_games():
    """Return {record_id: record} for every Switch / Switch 2 game in PIKI."""
    params = [
        ("filter[]", 'building:"0/Piki/"'),
        *[("filter[]", f'~format:"{code}"') for code in PLATFORMS],
        ("sort", "first_indexed desc"),
        ("limit", PAGE_SIZE),
        *[("field[]", f) for f in ("id", "title", "formats", "buildings", "images", "publishers", "year")],
    ]
    games, page = {}, 1
    while True:
        query = urllib.parse.urlencode(params + [("page", page)])
        data = http_json(f"{API_URL}?{query}")
        if data.get("status") != "OK":
            raise RuntimeError(f"Finna API error: {data}")
        records = data.get("records", [])
        for rec in records:
            games[rec["id"]] = rec
        if not records or page * PAGE_SIZE >= data["resultCount"]:
            return games
        page += 1


def platform_of(rec):
    for fmt in rec.get("formats", []):
        if fmt["value"] in PLATFORMS:
            return PLATFORMS[fmt["value"]]
    return "Nintendo Switch"


def clean_title(rec):
    return TITLE_SUFFIX.sub("", rec.get("title", "")).strip() or rec["id"]


def municipalities(rec):
    # Level-1 building facets are municipalities, e.g. "1/Piki/1/" = Tampere.
    return sorted({b["translated"] for b in rec.get("buildings", []) if b["value"].startswith("1/Piki/")})


def build_embed(rec):
    fields = [{"name": "Platform", "value": platform_of(rec), "inline": True}]
    if rec.get("year"):
        fields.append({"name": "Year", "value": rec["year"], "inline": True})
    if rec.get("publishers"):
        fields.append({"name": "Publisher", "value": ", ".join(rec["publishers"]), "inline": True})
    if places := municipalities(rec):
        fields.append({"name": "Libraries", "value": ", ".join(places)[:1024]})
    embed = {
        "title": clean_title(rec)[:256],
        "url": RECORD_URL.format(rec["id"]),
        "color": SWITCH_RED,
        "fields": fields,
    }
    if rec.get("images"):
        embed["thumbnail"] = {"url": COVER_BASE + rec["images"][0]}
    return embed


def post_discord(webhook, payload):
    for _ in range(5):
        try:
            http_json(webhook, payload)
            return
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(float(json.loads(e.read() or b"{}").get("retry_after", 2)) + 0.5)
    raise RuntimeError("Discord kept rate limiting the webhook")


def load_state():
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(seen):
    text = json.dumps(dict(sorted(seen.items())), ensure_ascii=False, indent=2)
    STATE_FILE.write_text(text + "\n", encoding="utf-8", newline="\n")


def label(rec):
    return f"{clean_title(rec)} ({platform_of(rec)})"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dry_run = "--dry-run" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK")
    if not webhook and not dry_run:
        sys.exit("DISCORD_WEBHOOK is not set (use --dry-run to test without it).")

    games = fetch_games()
    seen = load_state()

    if seen is None:
        # First run: remember the current catalogue without announcing all of it.
        print(f"No state file; seeding with {len(games)} games, nothing posted.")
        if not dry_run:
            save_state({gid: label(rec) for gid, rec in games.items()})
        return

    new = [rec for gid, rec in games.items() if gid not in seen]
    print(f"{len(games)} games in catalogue, {len(new)} new.")
    if not new:
        return

    # Each batch is (message payload, records it announces).
    if len(new) > FLOOD_LIMIT:
        batches = [({
            "content": f"🎮 {len(new)} new Switch / Switch 2 records appeared in PIKI at once "
                       "(possibly a catalogue re-index). Browse the newest: <" + SEARCH_URL + ">",
        }, new)]
    else:
        batches = []
        for i in range(0, len(new), EMBEDS_PER_MESSAGE):
            chunk = new[i:i + EMBEDS_PER_MESSAGE]
            msg = {"embeds": [build_embed(rec) for rec in chunk]}
            if i == 0:
                msg["content"] = "🎮 New in PIKI libraries"
            batches.append((msg, chunk))

    if dry_run:
        print(json.dumps([msg for msg, _ in batches], ensure_ascii=False, indent=2))
        return

    try:
        for msg, chunk in batches:
            post_discord(webhook, msg)
            # Only remember games once Discord has accepted them, so failures retry next run.
            seen.update({rec["id"]: label(rec) for rec in chunk})
    finally:
        save_state(seen)


if __name__ == "__main__":
    main()
