FROM python:3.14-slim

# osmium-tool cuts areas and buildings out of Geofabrik extracts
RUN apt-get update \
 && apt-get install -y --no-install-recommends osmium-tool ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PIP_DEFAULT_TIMEOUT=120 PIP_RETRIES=5 PIP_NO_CACHE_DIR=1
# dependencies in their own layer, so code changes don't reinstall them
COPY pyproject.toml LICENSE ./
RUN mkdir tak_buildings3d && touch tak_buildings3d/__init__.py README.md && pip install ".[web]" \
 && pip uninstall -y tak-buildings3d && rm -rf tak_buildings3d README.md build *.egg-info
COPY README.md ./
COPY tak_buildings3d ./tak_buildings3d
RUN pip install --no-deps .

COPY docker-entrypoint.sh /usr/local/bin/

ENV TAK_BUILDINGS3D_HOME=/data \
    GDAL_DISABLE_READDIR_ON_OPEN=EMPTY_DIR \
    PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8000
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["tak-buildings3d", "serve", "--host", "0.0.0.0", "--port", "8000"]
