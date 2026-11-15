"""tak-buildings3d command line.

  tak-buildings3d build --lat -35.2626 --lon 149.1440 --radius 500 --name ainslie
  tak-buildings3d build --pbf australia-latest.osm.pbf --polygon area.geojson --name mytown
  tak-buildings3d regions [--search australia]   list Geofabrik extracts
  tak-buildings3d download australia             download + reduce to buildings-only .pbf
  tak-buildings3d serve [--port 8000]            web app

Without --pbf, buildings come from the Overpass API (fine for a suburb). For a town or city,
download a Geofabrik extract first.
"""
import argparse
import sys
from pathlib import Path

from . import imagery, pipeline, regions


def _build(a, ap):
    """`tak-buildings3d build`: turn the arguments into pipeline.Params and run the build."""
    if a.polygon:
        clip = pipeline.geojson_geometry(a.polygon)
    elif a.bbox:
        clip = pipeline.bbox(*(float(v) for v in a.bbox.split(",")))
    elif a.lat is not None and a.lon is not None:
        clip = pipeline.circle(a.lat, a.lon, a.radius)
    else:
        ap.error("give --lat/--lon[/--radius], --bbox or --polygon")
    pbf = a.pbf
    if pbf and not Path(pbf).exists():
        pbf = regions.local_pbf(pbf)          # allow a downloaded region id
        if pbf is None:
            ap.error(f"no such file or downloaded region: {a.pbf}")
    src = a.imagery
    if a.imagery_url:
        imagery.validate_url(a.imagery_url)
        if not a.imagery_licence_ok:
            ap.error("--imagery-url needs --imagery-licence-ok (confirm the licence allows derived 3D models)")
        src = {"id": "cli", "name": a.imagery_url.split("/")[2], "url": a.imagery_url,
               "max_zoom": a.imagery_max_zoom, "attribution": a.imagery_attribution or a.imagery_url,
               "licence_confirmed": True}
    elif a.roofs != "osm" and not src:
        src = "auto"
    pipeline.run(pipeline.Params(name=a.name, clip=clip, pbf=pbf, roofs=a.roofs, imagery=src,
                                 roof_mpp=a.roof_mpp, roof_quality=a.roof_quality, tile_cell=a.tile_cell))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0].startswith("-") and argv[0] not in ("-h", "--help"):
        argv = ["build"] + argv               # no subcommand means build: tak-buildings3d --lat ... --name ...
    ap = argparse.ArgumentParser(prog="tak-buildings3d", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="build 3D Tiles for an area")
    b.add_argument("--name", required=True)
    b.add_argument("--lat", type=float)
    b.add_argument("--lon", type=float)
    b.add_argument("--radius", type=float, default=300, help="metres (with --lat/--lon)")
    b.add_argument("--bbox", help="min_lon,min_lat,max_lon,max_lat")
    b.add_argument("--polygon", help="GeoJSON file (Polygon/MultiPolygon/Feature[Collection])")
    b.add_argument("--pbf", help="local .osm.pbf, or the id of a downloaded Geofabrik region")
    b.add_argument("--roofs", default="osm", choices=["osm", "colour", "texture"],
                   help="osm: tag-driven colours; colour: one imagery colour per roof; "
                        "texture: imagery roof textures")
    b.add_argument("--imagery", help="imagery source id (naip, pdok, swisstopo, ign, s2cloudless, "
                                     "a saved one) or 'auto' (default: best source covering the area)")
    b.add_argument("--imagery-url", help="XYZ template with {z} {x} {y} (or {-y})")
    b.add_argument("--imagery-max-zoom", type=int, default=19)
    b.add_argument("--imagery-attribution", default="")
    b.add_argument("--imagery-licence-ok", action="store_true",
                   help="confirm the imagery licence allows derived 3D models")
    b.add_argument("--roof-mpp", type=float, default=None,
                   help="roof texture metres/pixel (texture mode; default: the imagery's own resolution, "
                        "but not finer than 0.25)")
    b.add_argument("--roof-quality", type=int, default=80, help="roof texture JPEG quality")
    b.add_argument("--tile-cell", type=float, default=150.0, help="leaf tile size (m)")

    r = sub.add_parser("regions", help="list Geofabrik extracts")
    r.add_argument("--search", default="")
    d = sub.add_parser("download", help="download a Geofabrik extract and reduce it to buildings")
    d.add_argument("region", help="Geofabrik id from `tak-buildings3d regions`, e.g. australia, berlin, us/california")
    s = sub.add_parser("serve", help="run the web app")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)

    a = ap.parse_args(argv)
    if a.cmd == "build":
        _build(a, b)
    elif a.cmd == "regions":
        for reg in regions.index():
            if a.search.lower() in (reg["id"] + " " + reg["name"]).lower():
                print(f"{reg['id']:40s} {reg['name']}  ({reg.get('parent') or '-'})")
    elif a.cmd == "download":
        regions.download(a.region, log=print)
    elif a.cmd == "serve":
        import uvicorn
        uvicorn.run("tak_buildings3d.web.app:app", host=a.host, port=a.port)


if __name__ == "__main__":
    main()
