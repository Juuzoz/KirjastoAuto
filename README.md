# KirjastoAuto

Posts a Discord message when a new **Nintendo Switch** or **Switch 2** game is added to the
[PIKI libraries](https://piki.finna.fi/) (Pirkanmaa), and a quieter message (no ping) when a game already in
the catalogue reaches a library that didn't have it before. All Tampere city library branches count as one library.

A GitHub Actions workflow runs [`check.py`](check.py) every 30 minutes, started by a
[cron-job.org](https://cron-job.org) job that calls the workflow dispatch API. It fetches all Switch / Switch 2
records held by PIKI from the [Finna API](https://api.finna.fi/), compares them with
[`seen_games.json`](seen_games.json), posts the new ones to a Discord webhook and commits the updated list.

## Setup

1. In Discord: *Server Settings → Integrations → Webhooks → New Webhook*, pick a channel, *Copy Webhook URL*.
2. In GitHub: *Settings → Secrets and variables → Actions → New repository secret*,
   name `DISCORD_WEBHOOK`, value = the webhook URL.
3. Optional, to get a push notification: in Discord enable *Settings → Advanced → Developer Mode*, right-click
   your name → *Copy User ID*. In GitHub: *Settings → Secrets and variables → Actions → Variables →
   New repository variable*, name `DISCORD_USER_ID`, value = that ID. New-game messages will then mention you.
4. *Actions* tab → *Check for new Switch games* → *Run workflow* to test it.
5. Create a fine-grained personal access token for this repository with *Actions: Read and write*, then a
   cron-job.org job that runs every 30 minutes:
   `POST https://api.github.com/repos/Juuzoz/KirjastoAuto/actions/workflows/check.yml/dispatches`
   with body `{"ref":"main"}` and headers `Authorization: Bearer <token>`,
   `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`, `Content-Type: application/json`.

## Local testing

```bash
python check.py --dry-run
```

Prints what would be posted without contacting Discord or changing `seen_games.json`.
If `seen_games.json` is missing, the next real run re-creates it from the current catalogue without posting anything.
