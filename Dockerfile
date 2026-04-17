FROM python:3.12-alpine

WORKDIR /app

# Only need requests — no more geopandas/GDAL/Pillow
RUN pip install --no-cache-dir requests

COPY post_spc_outlook.py ./

# Persistent state (last-seen pubDates)
VOLUME /app/data
ENV FEED_STATE_PATH=/app/data/feed_state.json
ENV POLL_INTERVAL=60

CMD ["python", "-u", "post_spc_outlook.py"]
