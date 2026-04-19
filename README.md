# spc-bluesky-bot

A Bluesky bot that automatically posts the official NWS Storm Prediction Center (SPC) Day 1, 2, and 3 Convective Outlook maps shortly after they are issued.

## How it works

The bot polls the [SPC Convective Outlook RSS feed](https://www.spc.noaa.gov/products/spcacrss.xml) every 60 seconds. When a new outlook is published, it downloads the official categorical outlook PNG directly from SPC and posts it to Bluesky, along with the risk headline from the forecast narrative. Each updated day is posted separately so every post corresponds to a single official SPC release.

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
- `PROPAGATION_DELAY` — seconds to wait after detecting a feed update before fetching the image, so SPC's CDN has time to serve the new PNG (default: `45`, set to `0` to disable)

Image downloads bypass CDN caches via a cache-busting query string and `Cache-Control` headers, so stale images aren't served after an SPC update.

State (last-seen pubDates) is persisted to a Docker volume so the bot won't re-post after restarts.
