"""Post new Nintendo Switch / Switch 2 games in the PIKI libraries to Discord.

Fetches every Switch and Switch 2 record held by PIKI (Pirkanmaa) libraries
from the Finna API, compares them against seen_games.json, and posts to a
Discord webhook (DISCORD_WEBHOOK env var):

- new games, mentioning DISCORD_USER_ID (if set) so it becomes a push notification
- known games that reached a library they were not in before, without a mention

    python check.py            # normal run
    python check.py --dry-run  # print what would be posted, change nothing
    python check.py --full     # include the text search regardless of the time
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_URL = "https://api.finna.fi/v1/search"
RECORD_URL = "https://piki.finna.fi/Record/{}"
SEARCH_URL = ("https://piki.finna.fi/Search/Results?filter%5B%5D=~format_ext_str_mv%3A%222%2FGame%2FVideoGame%2FSwitch%2F%22"
              "&filter%5B%5D=~format_ext_str_mv%3A%222%2FGame%2FVideoGame%2FSwitch2%2F%22&sort=first_indexed+desc")
COVER_BASE = "https://finna.fi"
USER_AGENT = "KirjastoAuto (+https://github.com/Juuzoz/KirjastoAuto)"
STATE_FILE = Path(__file__).with_name("seen_games.json")

PLATFORMS = {
    "2/Game/VideoGame/Switch/": "Nintendo Switch",
    "2/Game/VideoGame/Switch2/": "Nintendo Switch 2",
}
PAGE_SIZE = 100  # the Finna API's maximum
TEXT_SEARCH_EVERY_HOURS = 4
# More new games than this in one run most likely means Finna re-indexed
# records under new IDs, so post one summary instead of flooding the channel.
FLOOD_LIMIT = 30
EMBEDS_PER_MESSAGE = 10  # Discord's maximum
SWITCH_RED = 0xE60012
SPREAD_GREY = 0x99AAB5

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


def finna_json(url):
    """GET from the Finna API, retrying when it is rate limiting or briefly unavailable."""
    for wait in (10, 30, None):
        try:
            return http_json(url)
        except (urllib.error.URLError, TimeoutError) as e:
            transient = not isinstance(e, urllib.error.HTTPError) or e.code == 429 or e.code >= 500
            if not transient or wait is None:
                raise
            print(f"Finna request failed ({e}), retrying in {wait}s")
            time.sleep(wait)


def search_all(extra_params):
    """Return {record_id: record} for every PIKI record matching the extra search params."""
    params = [
        ("filter[]", 'building:"0/Piki/"'),
        *extra_params,
        ("sort", "id asc"),  # unique, so paging can't skip or repeat records
        ("limit", PAGE_SIZE),
        *[("field[]", f) for f in ("id", "title", "formats", "physicalDescriptions",
                                   "buildings", "images", "publishers", "year")],
    ]
    found, page = {}, 1
    while True:
        query = urllib.parse.urlencode(params + [("page", page)])
        data = finna_json(f"{API_URL}?{query}")
        if data.get("status") != "OK":
            raise RuntimeError(f"Finna API error: {data}")
        records = data.get("records", [])
        for rec in records:
            found[rec["id"]] = rec
        if not records or page * PAGE_SIZE >= data["resultCount"]:
            return found
        page += 1


def text_search_due(now):
    """Whether this run should also do the text search: once every 4 hours.

    Runs come every 30 minutes (:00 and :30), so this picks the first run of
    every fourth UTC hour.
    """
    return now.hour % TEXT_SEARCH_EVERY_HOURS == 0 and now.minute < 30


def fetch_games(text_search=True):
    """Return {record_id: record} for every Switch / Switch 2 game in PIKI.

    Finna merges records of the same game from different library networks, and
    search filters match the merged record, so another network cataloguing a
    game less precisely can hide PIKI's copy from a `format` filter. Broad
    searches gather candidates, then each PIKI record is judged by its own data.

    The format search finds nearly everything. The text search additionally
    catches games with no platform code in any network's record; it costs as
    many requests again, so it can be skipped on most runs.
    """
    candidates = search_all([("filter[]", f'~format_ext_str_mv:"{code}"') for code in PLATFORMS])
    if text_search:
        candidates.update(search_all([
            ("filter[]", 'format_ext_str_mv:"1/Game/VideoGame/"'),
            ("lookfor", '"Nintendo Switch"'),
        ]))
    games = {}
    for gid, rec in candidates.items():
        if platform := own_platform(rec):
            rec["platform"] = platform
            games[gid] = rec
    return games


SWITCH_2_TEXT = re.compile(r"nintendo\s+switch\s*2", re.IGNORECASE)
SWITCH_TEXT = re.compile(r"nintendo\s+switch", re.IGNORECASE)
OTHER_PLATFORM_TEXT = re.compile(r"\b(ps\s*[345]|playstation|xbox|wii\s*u|3ds|pc|windows)\b", re.IGNORECASE)


def own_platform(rec):
    """Platform name from the record's own catalogue data, or None if it isn't a Switch / Switch 2 game."""
    formats = {f["value"] for f in rec.get("formats", [])}
    if "1/Game/VideoGame/" not in formats:
        return None
    text = " ".join([rec.get("title", "")] + (rec.get("physicalDescriptions") or []))
    if OTHER_PLATFORM_TEXT.search(text) and not SWITCH_TEXT.search(text):
        return None
    platforms = {v for v in formats if v.startswith("2/Game/VideoGame/")}
    for code, name in sorted(PLATFORMS.items(), reverse=True):  # Switch 2 first
        if code in platforms:
            return name
    if platforms:  # catalogued as some other platform
        return None
    # No platform level in the record at all: fall back to its title and
    # physical description, e.g. "1 muistikortti (Nintendo Switch)".
    if SWITCH_2_TEXT.search(text):
        return "Nintendo Switch 2"
    if SWITCH_TEXT.search(text):
        return "Nintendo Switch"
    return None


def platform_of(rec):
    return rec["platform"]


def clean_title(rec):
    return TITLE_SUFFIX.sub("", rec.get("title", "")).strip() or rec["id"]


def libraries(rec):
    # Level-2 building facets are library organisations, e.g. "2/Piki/8/803/" =
    # Viialan kirjasto. All of Tampere's branches share one entry. Mobile
    # libraries (kirjastoauto) are left out.
    return sorted({b["translated"] for b in rec.get("buildings", [])
                   if b["value"].startswith("2/Piki/") and "kirjastoauto" not in b["translated"].lower()})


def base_embed(rec, color, fields):
    embed = {
        "title": clean_title(rec)[:256],
        "url": RECORD_URL.format(rec["id"]),
        "color": color,
        "fields": fields,
    }
    if rec.get("images"):
        embed["thumbnail"] = {"url": COVER_BASE + rec["images"][0]}
    return embed


def new_game_embed(rec):
    fields = [{"name": "Platform", "value": platform_of(rec), "inline": True}]
    if rec.get("year"):
        fields.append({"name": "Year", "value": rec["year"], "inline": True})
    if rec.get("publishers"):
        fields.append({"name": "Publisher", "value": ", ".join(rec["publishers"]), "inline": True})
    if libs := libraries(rec):
        fields.append({"name": "Libraries", "value": ", ".join(libs)[:1024]})
    return base_embed(rec, SWITCH_RED, fields)


def spread_embed(rec, added):
    return base_embed(rec, SPREAD_GREY, [
        {"name": "Platform", "value": platform_of(rec), "inline": True},
        {"name": "Now also at", "value": ", ".join(added)[:1024]},
        {"name": "All libraries", "value": ", ".join(libraries(rec))[:1024]},
    ])


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
    """Return {record_id: {"title": ..., "libraries": [...] or None}}, or None if there is no state yet."""
    if not STATE_FILE.exists():
        return None
    seen = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    # Older state files stored only the title; their libraries get filled in silently.
    return {gid: v if isinstance(v, dict) else {"title": v, "libraries": None} for gid, v in seen.items()}


def save_state(seen):
    # One game per line keeps the git history readable.
    lines = [f"  {json.dumps(gid)}: {json.dumps(entry, ensure_ascii=False)}" for gid, entry in sorted(seen.items())]
    STATE_FILE.write_text("{\n" + ",\n".join(lines) + "\n}\n", encoding="utf-8", newline="\n")


def label(rec):
    return f"{clean_title(rec)} ({platform_of(rec)})"


def state_entry(rec, libs=None):
    return {"title": label(rec), "libraries": libs if libs is not None else libraries(rec)}


def new_game_batches(new, mention, allowed_mentions):
    """Messages announcing brand-new games, pinging the configured user."""
    if len(new) > FLOOD_LIMIT:
        return [({
            "content": f"{mention}🎮 {len(new)} new Switch / Switch 2 records appeared in PIKI at once "
                       "(possibly a catalogue re-index). Browse the newest: <" + SEARCH_URL + ">",
            "allowed_mentions": allowed_mentions,
        }, {rec["id"]: state_entry(rec) for rec in new})]
    batches = []
    for i in range(0, len(new), EMBEDS_PER_MESSAGE):
        chunk = new[i:i + EMBEDS_PER_MESSAGE]
        msg = {"embeds": [new_game_embed(rec) for rec in chunk], "allowed_mentions": allowed_mentions}
        if i == 0:
            titles = ", ".join(label(rec) for rec in new)
            msg["content"] = f"{mention}🎮 New in PIKI: {titles}"[:2000]
        batches.append((msg, {rec["id"]: state_entry(rec) for rec in chunk}))
    return batches


def spread_batches(spread, seen):
    """Messages about known games reaching more libraries. These never ping."""
    no_pings = {"parse": []}

    def updates(items):
        return {rec["id"]: state_entry(rec, sorted(set(seen[rec["id"]]["libraries"]) | set(added)))
                for rec, added in items}

    if len(spread) > FLOOD_LIMIT:
        return [({
            "content": f"📚 {len(spread)} games were added to more PIKI libraries at once "
                       "(possibly a catalogue change).",
            "allowed_mentions": no_pings,
        }, updates(spread))]
    batches = []
    for i in range(0, len(spread), EMBEDS_PER_MESSAGE):
        chunk = spread[i:i + EMBEDS_PER_MESSAGE]
        msg = {"embeds": [spread_embed(rec, added) for rec, added in chunk], "allowed_mentions": no_pings}
        if i == 0:
            msg["content"] = "📚 More libraries got these games"
        batches.append((msg, updates(chunk)))
    return batches


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    dry_run = "--dry-run" in sys.argv
    webhook = os.environ.get("DISCORD_WEBHOOK")
    if not webhook and not dry_run:
        sys.exit("DISCORD_WEBHOOK is not set (use --dry-run to test without it).")

    seen = load_state()
    # Seeding always uses every search, so the first state file is complete.
    text_search = seen is None or "--full" in sys.argv or text_search_due(datetime.now(timezone.utc))
    games = fetch_games(text_search)
    print(f"Text search {'included' if text_search else 'skipped'} this run.")

    if seen is None:
        # First run: remember the current catalogue without announcing all of it.
        print(f"No state file; seeding with {len(games)} games, nothing posted.")
        if not dry_run:
            save_state({gid: state_entry(rec) for gid, rec in games.items()})
        return

    # Games tracked before library tracking existed: record their libraries without announcing.
    for gid, entry in seen.items():
        if entry["libraries"] is None and gid in games:
            entry["libraries"] = libraries(games[gid])

    new = [rec for gid, rec in games.items() if gid not in seen]
    # Libraries are only ever added to a game's list, so a library that drops
    # out and comes back later is not announced twice.
    spread = []
    for gid, rec in games.items():
        if gid in seen and seen[gid]["libraries"] is not None:
            if added := sorted(set(libraries(rec)) - set(seen[gid]["libraries"])):
                spread.append((rec, added))
    print(f"{len(games)} games in catalogue, {len(new)} new, {len(spread)} in more libraries.")

    # Mentioning a user makes Discord send them a push notification. Mentions
    # only ping from the message text, not from embeds, so the first message's
    # text also names the games for the notification preview.
    user_id = os.environ.get("DISCORD_USER_ID", "").strip()
    mention = f"<@{user_id}> " if user_id else ""
    allowed_mentions = {"users": [user_id] if user_id else []}

    # Each batch is (message payload, state updates to apply once it is posted).
    batches = new_game_batches(new, mention, allowed_mentions) if new else []
    batches += spread_batches(spread, seen) if spread else []

    if dry_run:
        print(json.dumps([msg for msg, _ in batches], ensure_ascii=False, indent=2))
        return

    try:
        for msg, updates in batches:
            post_discord(webhook, msg)
            # Only remember games once Discord has accepted them, so failures retry next run.
            seen.update(updates)
    finally:
        save_state(seen)


if __name__ == "__main__":
    main()
