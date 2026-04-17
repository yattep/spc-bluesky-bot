"""
post_spc_outlook.py — SPC Convective Outlook → Bluesky bot

How it works:
  1. Poll the SPC Convective Outlook RSS feed every POLL_INTERVAL seconds.
  2. For each <item>, swap the link's .html extension for .png to get the
     official outlook image URL.
     Example:
       link: .../day1otlk_2000.html
       img:  .../day1otlk_2000.png
     For Day 3 the pattern is .../day3otlk.html -> .../day3otlk.png
  3. Track last-seen pubDate per day in feed_state.json. When any day has
     a new pubDate, download all three current PNGs and post them together.
  4. Includes the risk headline from the feed's description in the post.

Environment variables:
  BSKY_HANDLE          — Bluesky handle (required)
  BSKY_APP_PASSWORD    — Bluesky app password (required)
  POLL_INTERVAL        — Seconds between feed checks (default: 60)
  FEED_STATE_PATH      — Path to feed state file (default: ./data/feed_state.json)
  RUN_ONCE             — Set to "1" for single-run mode (for GitHub Actions)
"""

import io
import json
import os
import re
import signal
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BSKY_HANDLE = os.environ["BSKY_HANDLE"]
BSKY_APP_PASSWORD = os.environ["BSKY_APP_PASSWORD"]
BSKY_API = "https://bsky.social/xrpc"

RSS_URL = "https://www.spc.noaa.gov/products/spcacrss.xml"
USER_AGENT = "SPCBlueskyBot/4.0 (+https://github.com/yattep/spc-bluesky-bot)"

POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))
FEED_STATE_PATH = os.environ.get(
    "FEED_STATE_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "feed_state.json"),
)
RUN_ONCE = os.environ.get("RUN_ONCE", "0") == "1"

# Match links like .../day1otlk.html, .../day1otlk_1300.html, .../day3otlk.html
DAY_LINK_RE = re.compile(
    r"/day([123])otlk(?:_(\d{4}))?\.html", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------
_shutdown = False


def _handle_signal(sig, frame):
    global _shutdown
    print(f"\nReceived signal {sig}, shutting down after current cycle...")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def load_state():
    if os.path.exists(FEED_STATE_PATH):
        try:
            with open(FEED_STATE_PATH, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def save_state(state):
    os.makedirs(os.path.dirname(FEED_STATE_PATH), exist_ok=True)
    with open(FEED_STATE_PATH, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# RSS feed parsing
# ---------------------------------------------------------------------------

def fetch_feed():
    """Download and parse the SPC convective outlook RSS feed.

    Returns dict of {day_number: entry_dict} containing the latest item
    for each day. entry_dict has keys: pub_date, link, image_url, title,
    description.
    """
    resp = requests.get(
        RSS_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    latest = {}  # day -> (pub_datetime, entry_dict)

    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        match = DAY_LINK_RE.search(link)
        if not match:
            continue
        day = int(match.group(1))

        pub_str = (item.findtext("pubDate") or "").strip()
        try:
            pub_dt = parsedate_to_datetime(pub_str)
            if pub_dt.tzinfo is None:
                pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue

        # Build image URL by swapping .html -> .png
        image_url = re.sub(r"\.html$", ".png", link, flags=re.IGNORECASE)

        entry = {
            "pub_date": pub_dt.isoformat(),
            "link": link,
            "image_url": image_url,
            "title": (item.findtext("title") or "").strip(),
            "description": (item.findtext("description") or "").strip(),
        }

        if day not in latest or pub_dt > latest[day][0]:
            latest[day] = (pub_dt, entry)

    return {day: entry for day, (_, entry) in latest.items()}


def extract_risk_headline(description):
    """Pull the '...THERE IS A ... RISK OF ...' headline from the narrative."""
    if not description:
        return None
    match = re.search(r"\.\.\.\s*(THERE IS[^.]+?)\s*\.\.\.", description, re.IGNORECASE)
    if match:
        headline = match.group(1).strip()
        headline = re.sub(r"\s+", " ", headline)
        return headline
    return None


# ---------------------------------------------------------------------------
# Image fetching
# ---------------------------------------------------------------------------

def fetch_image(url):
    """Download the outlook PNG and return (bytes, mime_type)."""
    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "image/png").split(";")[0].strip()
    return resp.content, content_type


# ---------------------------------------------------------------------------
# Bluesky API
# ---------------------------------------------------------------------------

def login():
    resp = requests.post(
        f"{BSKY_API}/com.atproto.server.createSession",
        json={"identifier": BSKY_HANDLE, "password": BSKY_APP_PASSWORD},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["accessJwt"], data["did"]


def upload_image(token, image_bytes, mime_type="image/png"):
    for attempt in range(3):
        resp = requests.post(
            f"{BSKY_API}/com.atproto.repo.uploadBlob",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": mime_type,
            },
            data=image_bytes,
            timeout=60,
        )
        if resp.status_code == 504 and attempt < 2:
            print(f"    Upload timeout, retrying ({attempt + 2}/3)...")
            time.sleep(5)
            continue
        resp.raise_for_status()
        return resp.json()["blob"]


def post_to_bluesky(token, did, text, images):
    embed_images = [{"alt": img["alt"], "image": img["blob"]} for img in images]
    record = {
        "$type": "app.bsky.feed.post",
        "text": text,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        "embed": {
            "$type": "app.bsky.embed.images",
            "images": embed_images,
        },
    }
    resp = requests.post(
        f"{BSKY_API}/com.atproto.repo.createRecord",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "repo": did,
            "collection": "app.bsky.feed.post",
            "record": record,
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Main cycle
# ---------------------------------------------------------------------------

def check_and_post():
    """Returns True if a post was made."""
    state = load_state()
    last_seen = state.get("last_seen", {})  # {"1": iso_str, "2": ..., "3": ...}

    try:
        feed_items = fetch_feed()
    except Exception as e:
        print(f"  Error fetching RSS feed: {e}")
        return False

    if not feed_items:
        print("  No outlook items found in feed.")
        return False

    # Determine which days have new pubDates
    updated_days = []
    for day in sorted(feed_items.keys()):
        entry = feed_items[day]
        prev = last_seen.get(str(day))
        if prev != entry["pub_date"]:
            updated_days.append(day)
            print(f"  Day {day} updated: {entry['pub_date']} (was {prev})")
        else:
            print(f"  Day {day} unchanged ({entry['pub_date']})")

    if not updated_days:
        return False

    print(f"Found {len(updated_days)} updated outlook(s) — fetching images...")

    # Download and upload all three days so the post is always a complete set
    blobs = []
    token, did = login()

    for day in sorted(feed_items.keys()):
        entry = feed_items[day]
        try:
            print(f"  Downloading Day {day}: {entry['image_url']}")
            image_bytes, mime_type = fetch_image(entry["image_url"])
            print(f"  Uploading Day {day} ({len(image_bytes) // 1024} KB, {mime_type})...")
            blob = upload_image(token, image_bytes, mime_type=mime_type)
            blobs.append({
                "blob": blob,
                "alt": f"SPC Convective Outlook Day {day} Categorical Map",
            })
        except Exception as e:
            print(f"  Error processing Day {day}: {e}")

    if not blobs:
        print("  No images uploaded; skipping post.")
        return False

    # Build post text
    now_utc = datetime.now(timezone.utc).strftime("%H:%Mz %b %d, %Y")
    updated_labels = ", ".join(f"Day {d}" for d in sorted(updated_days))

    primary_day = min(updated_days)  # Day 1 is highest priority if present
    headline = extract_risk_headline(feed_items[primary_day].get("description", ""))

    lines = [f"🌪️ SPC Convective Outlooks — {now_utc}"]
    if headline:
        if len(headline) > 180:
            headline = headline[:177] + "..."
        lines.append("")
        lines.append(headline)
    lines.append("")
    lines.append(f"Updated: {updated_labels}")
    lines.append("spc.noaa.gov/products/outlook/")
    post_text = "\n".join(lines)

    print("  Posting to Bluesky...")
    result = post_to_bluesky(token, did, post_text, blobs)
    print(f"  Posted: {result.get('uri', result)}")

    # Save state only after successful post
    state["last_seen"] = {
        str(day): entry["pub_date"] for day, entry in feed_items.items()
    }
    state["last_post_utc"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return True


def main():
    if RUN_ONCE:
        print("Running in single-shot mode...")
        check_and_post()
        return

    print(f"Starting SPC outlook polling loop (RSS, every {POLL_INTERVAL}s)...")
    print(f"  Feed: {RSS_URL}")
    print(f"  State: {FEED_STATE_PATH}")
    print()

    while not _shutdown:
        try:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
            print(f"[{ts}] Checking RSS feed...")
            check_and_post()
        except Exception as e:
            print(f"  Error during check cycle: {e}")

        for _ in range(POLL_INTERVAL):
            if _shutdown:
                break
            time.sleep(1)

    print("Shutdown complete.")


if __name__ == "__main__":
    main()
