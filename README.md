# spc-bluesky-bot

A Bluesky bot that automatically posts the official NWS Storm Prediction Center (SPC) Day 1, 2, and 3 Convective Outlook maps shortly after they are issued.

## How it works

The bot polls the [SPC Convective Outlook RSS feed](https://www.spc.noaa.gov/products/spcacrss.xml) every 60 seconds. When a new outlook is published, it downloads the official categorical outlook PNGs directly from SPC and posts all three days together to Bluesky, along with the risk headline from the forecast narrative.

## Running it

Designed to run as a Docker container. Clone the repo and deploy with Docker Compose:

```bash
docker compose up -d
```

Required environment variables:

- `BSKY_HANDLE` — your Bluesky handle
- `BSKY_APP_PASSWORD` — a Bluesky app password ([generate one here](https://bsky.app/settings/app-passwords))

Optional:

- `POLL_INTERVAL` — seconds between feed checks (default: `60`)

State (last-seen pubDates) is persisted to a Docker volume so the bot won't re-post after restarts.
