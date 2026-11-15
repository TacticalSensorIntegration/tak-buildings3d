#!/bin/sh
# Run as an ordinary user that owns /data, so files in tak-buildings3d-data/ are yours on Linux hosts.
set -e
if [ "$(id -u)" = 0 ]; then
  uid=${PUID:-1000} gid=${PGID:-1000}
  mkdir -p /data/geotiff
  find /data \( ! -user "$uid" -o ! -group "$gid" \) -exec chown "$uid:$gid" {} + 2>/dev/null \
    || echo "note: could not change owner of files in /data (fine on Docker Desktop)"
  export HOME=/tmp
  exec setpriv --reuid="$uid" --regid="$gid" --clear-groups "$@"
fi
exec "$@"
