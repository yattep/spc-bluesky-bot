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
    from PIL import Image
    import io

    # Fetch basemap tiles from ESRI's public light gray basemap
    basemap_url = (
        "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/"
        "World_Light_Gray_Base/MapServer/export"
        "?bbox=-125,24,-66,50&bboxSR=4269&imageSR=4269"
        "&size=1600,1000&format=png&f=image"
    )
    base_resp = requests.get(basemap_url, timeout=15)
    base_resp.raise_for_status()
    base_img = Image.open(io.BytesIO(base_resp.content)).convert("RGBA")

    # Fetch the outlook overlay (transparent PNG)
    overlay_resp = requests.get(url, timeout=15)
    overlay_resp.raise_for_status()
    overlay_img = Image.open(io.BytesIO(overlay_resp.content)).convert("RGBA")

    # Composite outlook on top of basemap
    combined = Image.alpha_composite(base_img, overlay_img)

    # Boost color saturation
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Color(combined)
    combined = enhancer.enhance(1.6)  # 1.0 is original, increase to taste

    import zipfile
    import geopandas as gpd
    from PIL import ImageDraw

    # Download Census Bureau state boundaries shapefile
    shp_url = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_state_500k.zip"
    shp_resp = requests.get(shp_url, timeout=30)
    shp_resp.raise_for_status()

    # Extract and load shapefile
    with zipfile.ZipFile(io.BytesIO(shp_resp.content)) as z:
        z.extractall("/tmp/states")
    states = gpd.read_file("/tmp/states/cb_2023_us_state_500k.shp")

    # Filter to CONUS only
    states = states[~states["STUSPS"].isin(["AK", "HI", "PR", "VI", "GU", "MP", "AS"])]

    # Map geo coordinates to image pixels
    bbox_left, bbox_bottom, bbox_right, bbox_top = -125, 24, -66, 50
    img_width, img_height = 1600, 1000

    # Reproject states to Web Mercator to match the basemap
    states = states.to_crs(epsg=3857)

    # Web Mercator bounds for our bbox (-125, 24, -66, 50)
    from pyproj import Transformer
    transformer = Transformer.from_crs("epsg:4269", "epsg:3857", always_xy=True)
    left, bottom = transformer.transform(-125, 24)
    right, top = transformer.transform(-66, 50)

    img_width, img_height = 1600, 1000

    def geo_to_pixel(lon, lat):
        x = (lon - left) / (right - left) * img_width
        y = (1 - (lat - bottom) / (top - bottom)) * img_height
        return x, y

    # Draw state borders onto the image
    combined = combined.convert("RGBA")
    draw = ImageDraw.Draw(combined)

    for geom in states.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            draw.line(coords, fill=(40, 40, 40, 255), width=2)

    del draw

    # Draw state borders onto the image
    combined = combined.convert("RGBA")
    draw = ImageDraw.Draw(combined)

    for geom in states.geometry:
        polys = geom.geoms if geom.geom_type == "MultiPolygon" else [geom]
        for poly in polys:
            coords = [geo_to_pixel(lon, lat) for lon, lat in poly.exterior.coords]
            draw.line(coords, fill=(40, 40, 40, 255), width=2)

    del draw
    
    # Convert to RGB PNG for upload
    output = io.BytesIO()
    combined.convert("RGB").save(output, format="PNG")
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
