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

    img_width, img_height = 1600, 1000
    bbox_left, bbox_bottom, bbox_right, bbox_top = -125, 24, -66, 50

    def geo_to_pixel(lon, lat):
        x = (lon - bbox_left) / (bbox_right - bbox_left) * img_width
        y = (1 - (lat - bbox_bottom) / (bbox_top - bbox_bottom)) * img_height
        return (x, y)

    # Determine which day this URL is for based on layer ID
    if "layers=show:1&" in url:
        shp_url = "https://www.spc.noaa.gov/products/outlook/day1otlk-shp.zip"
        day = 1
    elif "layers=show:9&" in url:
        shp_url = "https://www.spc.noaa.gov/products/outlook/day2otlk-shp.zip"
        day = 2
    else:
        shp_url = "https://www.spc.noaa.gov/products/outlook/day3otlk-shp.zip"
        day = 3

    # SPC categorical risk colors by DN value
    RISK_COLORS = {
        2: (145, 208, 114, 200),   # Thunderstorm - light green
        3: (120, 173, 90, 200),    # Marginal - dark green
        4: (255, 255, 102, 220),   # Slight - yellow
        5: (255, 165, 0, 220),     # Enhanced - orange
        6: (255, 80, 80, 220),     # Moderate - red
        7: (255, 0, 255, 220),     # High - magenta
    }

    # Create base canvas
    base_img = Image.new("RGBA", (img_width, img_height), (150, 190, 220, 255))

    # Draw land masses on top of ocean-colored canvas
    land_url = "https://naciscdn.org/naturalearth/110m/physical/ne_110m_land.zip"
    land_resp = requests.get(land_url, timeout=30)
    land_resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(land_resp.content)) as z:
        z.extractall("/tmp/land")
    land = gpd.read_file("/tmp/land/ne_110m_land.shp")

    draw = ImageDraw.Draw(base_img)
    for geom in land.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            if len(coords) > 2:
                draw.polygon(coords, fill=(255, 251, 240, 255))
    del draw

    # Download SPC shapefile
    headers = {"User-Agent": "Mozilla/5.0"}
    shp_resp = requests.get(shp_url, headers=headers, timeout=30)
    shp_resp.raise_for_status()

    extract_path = f"/tmp/day{day}otlk"
    with zipfile.ZipFile(io.BytesIO(shp_resp.content)) as z:
        z.extractall(extract_path)

    # Find the categorical shapefile
    import os
    shp_files = [f for f in os.listdir(extract_path) if f.endswith(".shp") and "cat" in f.lower()]
    if not shp_files:
        shp_files = [f for f in os.listdir(extract_path) if f.endswith(".shp")]
    outlook = gpd.read_file(f"{extract_path}/{shp_files[0]}")
    outlook = outlook.to_crs(epsg=4326)

    # Draw risk polygons in order (lowest to highest so higher risks render on top)
    combined = base_img.copy()
    overlay = Image.new("RGBA", (img_width, img_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    for dn in sorted(RISK_COLORS.keys()):
        subset = outlook[outlook["DN"] == dn] if "DN" in outlook.columns else outlook[outlook["dn"] == dn]
        for geom in subset.geometry:
            polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
            for poly in polys:
                coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
                if len(coords) > 2:
                    draw.polygon(coords, fill=RISK_COLORS[dn])
    del draw

    combined = Image.alpha_composite(combined.convert("RGBA"), overlay)

    # Boost saturation
    enhancer = ImageEnhance.Color(combined)
    combined = enhancer.enhance(1.4)

    # Draw state borders
    shp_state_resp = requests.get(
        "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip",
        timeout=30
    )
    shp_state_resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(shp_state_resp.content)) as z:
        z.extractall("/tmp/states")
    states = gpd.read_file("/tmp/states/cb_2023_us_state_500k.shp")
    states = states[~states["STUSPS"].isin(["AK", "HI", "PR", "VI", "GU", "MP", "AS"])]
    states = states.to_crs(epsg=4326)

    combined = combined.convert("RGBA")
    draw = ImageDraw.Draw(combined)
    for geom in states.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            draw.line(coords, fill=(40, 40, 40, 255), width=2)
    del draw

    output = io.BytesIO()
    combined.convert("RGB").save(output, format="JPEG", quality=85, optimize=True)
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
