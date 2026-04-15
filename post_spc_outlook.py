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
        "url": "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer/export?layers=show:1&bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269&size=1600,1000&format=png&transparent=true&f=image",
        "label": "Day 1",
    },
    {
        "day": 2,
        "url": "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer/export?layers=show:9&bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269&size=1600,1000&format=png&transparent=true&f=image",
        "label": "Day 2",
    },
    {
        "day": 3,
        "url": "https://mapservices.weather.noaa.gov/vector/rest/services/outlooks/SPC_wx_outlks/MapServer/export?layers=show:17&bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269&size=1600,1000&format=png&transparent=true&f=image",
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


def upload_image(token, image_bytes, mime_type="image/jpeg"):
    import time
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
            print(f"Upload timeout, retrying ({attempt + 2}/3)...")
            time.sleep(5)
            continue
        resp.raise_for_status()
        return resp.json()["blob"]


def fetch_image(url):
    from PIL import Image, ImageEnhance, ImageDraw
    import io, zipfile
    import geopandas as gpd
    from pyproj import Transformer
    import numpy as np

    img_width, img_height = 1600, 1000

    # Use plain lat/lon (EPSG:4326) throughout — no projection conversion needed
    bbox_left, bbox_bottom, bbox_right, bbox_top = -125, 24, -66, 50

    def geo_to_pixel(lon, lat):
        x = (lon - bbox_left) / (bbox_right - bbox_left) * img_width
        y = (1 - (lat - bbox_bottom) / (bbox_top - bbox_bottom)) * img_height
        return (x, y)

    # Create light gray base canvas
    base_img = Image.new("RGBA", (img_width, img_height), (240, 240, 240, 255))

    # Download and draw country/ocean fill from Natural Earth
    ne_url = "https://naciscdn.org/naturalearth/110m/physical/ne_110m_ocean.zip"
    ne_resp = requests.get(ne_url, timeout=30)
    ne_resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(ne_resp.content)) as z:
        z.extractall("/tmp/ocean")
    ocean = gpd.read_file("/tmp/ocean/ne_110m_ocean.shp")

    draw = ImageDraw.Draw(base_img)
    for geom in ocean.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            if len(coords) > 2:
                draw.polygon(coords, fill=(180, 200, 220, 255))
    del draw

    # Fetch the outlook overlay from NOAA (lat/lon bbox)
    overlay_resp = requests.get(url, timeout=15)
    overlay_resp.raise_for_status()
    overlay_img = Image.open(io.BytesIO(overlay_resp.content)).convert("RGBA")

    # Composite outlook on top of basemap
    combined = Image.alpha_composite(base_img, overlay_img)

    # Boost saturation
    enhancer = ImageEnhance.Color(combined)
    combined = enhancer.enhance(1.6)

    # Download and draw state borders
    shp_url = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip"
    shp_resp = requests.get(shp_url, timeout=30)
    shp_resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(shp_resp.content)) as z:
        z.extractall("/tmp/states")
    states = gpd.read_file("/tmp/states/cb_2023_us_state_500k.shp")
    states = states[~states["STUSPS"].isin(["AK", "HI", "PR", "VI", "GU", "MP", "AS"])]

    # Download and draw country borders
    country_url = "https://naciscdn.org/naturalearth/110m/cultural/ne_110m_admin_0_countries.zip"
    country_resp = requests.get(country_url, timeout=30)
    country_resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(country_resp.content)) as z:
        z.extractall("/tmp/countries")
    countries = gpd.read_file("/tmp/countries/ne_110m_admin_0_countries.shp")

    combined = combined.convert("RGBA")
    draw = ImageDraw.Draw(combined)

    # Draw country borders (slightly thicker)
    for geom in countries.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            draw.line(coords, fill=(40, 40, 40, 255), width=3)

    # Draw state borders
    for geom in states.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            draw.line(coords, fill=(40, 40, 40, 255), width=2)

    del draw

    output = io.BytesIO()
    combined.convert("RGB").save(output, format="PNG")
    
    # Compress to keep file size manageable for upload
    output = io.BytesIO()
    final = combined.convert("RGB")
    final.save(output, format="JPEG", quality=85, optimize=True)
    return output.getvalue()

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
