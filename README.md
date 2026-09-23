# KirjastoAuto

Posts a Discord message when a new **Nintendo Switch** or **Switch 2** game is added to the
[PIKI libraries](https://piki.finna.fi/) (Pirkanmaa).

A GitHub Actions workflow runs [`check.py`](check.py) every 30 minutes. It fetches all Switch / Switch 2
records held by PIKI from the [Finna API](https://api.finna.fi/), compares them with
[`seen_games.json`](seen_games.json), posts the new ones to a Discord webhook and commits the updated list.

## Setup

1. In Discord: *Server Settings → Integrations → Webhooks → New Webhook*, pick a channel, *Copy Webhook URL*.
2. In GitHub: *Settings → Secrets and variables → Actions → New repository secret*,
   name `DISCORD_WEBHOOK`, value = the webhook URL.
3. *Actions* tab → *Check for new Switch games* → *Run workflow* to test it.

## Local testing

```bash
python check.py --dry-run
```

Prints what would be posted without contacting Discord or changing `seen_games.json`.
If `seen_games.json` is missing, the next real run re-creates it from the current catalogue without posting anything.
