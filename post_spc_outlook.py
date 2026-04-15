import os
import requests
from datetime import datetime, timezone

# --- Config ---
BSKY_HANDLE = os.environ["BSKY_HANDLE"]
BSKY_APP_PASSWORD = os.environ["BSKY_APP_PASSWORD"]
BSKY_API = "https://bsky.social/xrpc"

OUTLOOK_DAYS = [
    {
        "day": 1,
        "url": "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer/export?layers=show:1&bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269&size=800,500&format=png&transparent=false&f=image",
        "label": "Day 1",
    },
    {
        "day": 2,
        "url": "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer/export?layers=show:8&bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269&size=800,500&format=png&transparent=false&f=image",
        "label": "Day 2",
    },
    {
        "day": 3,
        "url": "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer/export?layers=show:15&bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269&size=800,500&format=png&transparent=false&f=image",
        "label": "Day 3",
    },
]


def login():
    resp = requests.post(
        f"{BSKY_API}/com.atproto.server.createSession",
        json={"identifier": BSKY_HANDLE, "password": BSKY_APP_PASSWORD},
    )
    resp.raise_for_status()
    data = resp.json()
    return data["accessJwt"], data["did"]


def upload_image(token, image_bytes, mime_type="image/png"):
    resp = requests.post(
        f"{BSKY_API}/com.atproto.repo.uploadBlob",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": mime_type,
        },
        data=image_bytes,
    )
    resp.raise_for_status()
    return resp.json()["blob"]


def fetch_image(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.content


def post_to_bluesky(token, did, text, images):
    """images: list of dicts with 'blob' and 'alt' keys"""
    embed_images = [
        {
            "alt": img["alt"],
            "image": img["blob"],
        }
        for img in images
    ]

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
    )
    resp.raise_for_status()
    return resp.json()


def main():
    print("Logging in to Bluesky...")
    token, did = login()

    blobs = []
    for outlook in OUTLOOK_DAYS:
        print(f"Fetching {outlook['label']} outlook image...")
        image_bytes = fetch_image(outlook["url"])

        print(f"Uploading {outlook['label']} image to Bluesky...")
        blob = upload_image(token, image_bytes)
        blobs.append({
            "blob": blob,
            "alt": f"SPC Convective Outlook {outlook['label']} Categorical Map",
        })

    now_utc = datetime.now(timezone.utc).strftime("%H:%Mz %b %d, %Y")
    post_text = (
        f"🌪️ SPC Convective Outlooks — {now_utc}\n\n"
        "Day 1 / Day 2 / Day 3 Categorical Maps\n\n"
        "spc.noaa.gov/products/outlook/"
    )

    print("Posting to Bluesky...")
    result = post_to_bluesky(token, did, post_text, blobs)
    print(f"Posted successfully: {result}")


if __name__ == "__main__":
    main()
