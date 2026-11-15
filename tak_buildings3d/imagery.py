"""XYZ (Web Mercator) imagery sources, tile fetching and per-roof crops.

Only sources whose licence allows derived 3D models are used: built-ins, or ones the user confirmed.
"""
import hashlib
import io
import json
import math
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import requests
from PIL import Image, ImageDraw
from shapely.geometry import Point, box, mapping, shape
from shapely.ops import unary_union
from shapely.prepared import prep

from . import USER_AGENT

UA = USER_AGENT
TILE = 256                         # px per Web Mercator tile
MERC_R = 20037508.342789244        # half the Web Mercator world width (m): pi * 6378137
Z0_MPP = 2 * MERC_R / TILE         # ground resolution of zoom 0 at the equator (m/px)
PREFETCH_WORKERS = 6               # parallel downloads, kept low to stay polite
COLOUR_MPP = 1.0                   # enough for a median colour per roof
TEXTURE_MAX_MPP = 2.0              # textured roofs need imagery at least this sharp
PHOTO_DEFAULT_MPP = 0.25           # finest default roof texture resolution
WORLD = [-180.0, -85.0, 180.0, 85.0]

# Built-in open imagery: each licence allows commercial use and derived works, with attribution at
# most (checked November 2026, see LICENSES.md; terms can change).
# coverage = [min_lon, min_lat, max_lon, max_lat]; regions/exclude refine it, see footprint().


def _wms13(url, layer):
    """WMS 1.3.0 GetMap URL template for 256 px Web Mercator tiles."""
    return (f"{url}?SERVICE=WMS&VERSION=1.3.0&REQUEST=GetMap&LAYERS={layer}&STYLES="
            "&CRS=EPSG:3857&BBOX={bbox}&WIDTH=256&HEIGHT=256&FORMAT=image/jpeg")


DL_ZERO = "https://www.govdata.de/dl-de/zero-2-0"
DL_BY = "https://www.govdata.de/dl-de/by-2-0"
BUILTIN = {
    "naip": {
        # builds read the 0.6 m COGs from Planetary Computer (naip_pc.py); the USGS ImageServer
        # is the map layer and the fallback where Planetary Computer has no scene
        "backend": "planetary_computer", "pc_collection": "naip",
        "name": "United States – USDA NAIP",
        "url": "https://imagery.nationalmap.gov/arcgis/rest/services/USGSNAIPImagery/ImageServer/"
               "exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&format=jpg&f=image",
        "max_zoom": 18, "resolution_m": 0.6, "coverage": [-125.0, 24.4, -66.9, 49.5], "regions": ["us"],
        "attribution": "Imagery: USDA NAIP (public domain), via Microsoft Planetary Computer "
                       "and USGS The National Map",
        "licence": "Public domain",
        "licence_url": "https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits",
    },
    "pdok": {
        "name": "Netherlands – PDOK Luchtfoto",
        "url": "https://service.pdok.nl/hwh/luchtfotorgb/wmts/v1_0/Actueel_orthoHR/EPSG:3857/{z}/{x}/{y}.jpeg",
        "max_zoom": 21, "resolution_m": 0.08, "coverage": [3.2, 50.7, 7.3, 53.6], "regions": ["netherlands"],
        "attribution": "Luchtfoto: Beeldmateriaal Nederland / PDOK, CC BY 4.0",
        "licence": "CC BY 4.0", "licence_url": "https://www.beeldmateriaal.nl/data-room",
    },
    "swisstopo": {
        "name": "Switzerland – swisstopo SWISSIMAGE",
        "url": "https://wmts.geo.admin.ch/1.0.0/ch.swisstopo.swissimage/default/current/3857/{z}/{x}/{y}.jpeg",
        "max_zoom": 20, "resolution_m": 0.1, "coverage": [5.9, 45.8, 10.5, 47.8], "regions": ["switzerland"],
        "attribution": "Imagery (c) swisstopo (SWISSIMAGE), free use with attribution",
        "licence": "swisstopo OGD (free use, attribution)",
        "licence_url": "https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices",
    },
    "ign": {
        "name": "France – IGN BD ORTHO",
        "url": "https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=ORTHOIMAGERY.ORTHOPHOTOS"
               "&STYLE=normal&TILEMATRIXSET=PM&FORMAT=image/jpeg&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [-5.2, 41.3, 9.6, 51.1], "regions": ["france"],
        "attribution": "Imagery (c) IGN BD ORTHO, Licence Ouverte / Etalab 2.0",
        "licence": "Etalab Open Licence 2.0", "licence_url": "https://cartes.gouv.fr/cgu/",
    },
    "pnoa": {
        "name": "Spain – IGN PNOA",
        "url": "https://www.ign.es/wmts/pnoa-ma?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=OI.OrthoimageCoverage"
               "&STYLE=default&TILEMATRIXSET=GoogleMapsCompatible&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/jpeg",
        "max_zoom": 19, "resolution_m": 0.25, "coverage": [-18.2, 27.6, 4.4, 43.8],
        "regions": ["spain", "canary-islands"],
        "exclude": [[-3.0, 35.25, -2.9, 35.33]],         # Melilla is Airbus satellite imagery, not PNOA
        "attribution": "Obra derivada de PNOA CC BY 4.0 scne.es",
        "licence": "CC BY 4.0", "licence_url": "https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf",
    },
    "dgt": {
        "name": "Portugal – DGT Ortofotos 2025",
        "url": "https://geo.dgterritorio.gov.pt/wms/ortos2025?SERVICE=WMS&VERSION=1.1.1&REQUEST=GetMap&LAYERS=Ortos2025-RGB"
               "&STYLES=&SRS=EPSG:3857&BBOX={bbox}&WIDTH=256&HEIGHT=256&FORMAT=image/jpeg",
        "max_zoom": 19, "resolution_m": 0.25, "coverage": [-9.683, 36.906, -6.1, 42.189], "regions": ["portugal"],
        "attribution": "Informação geográfica cedida pela Direção-Geral do Território (Ortofotos 2025), CC BY 4.0",
        "licence": "CC BY 4.0",
        "licence_url": "https://snig.dgterritorio.gov.pt/rndg/srv/api/records/96d83932-f957-40d6-a2ea-3a25751c4b2d",
    },
    "basemap-at": {
        "name": "Austria – basemap.at Orthofoto",
        "url": "https://mapsneu.wien.gv.at/basemap/bmaporthofoto30cm/normal/google3857/{z}/{y}/{x}.jpeg",
        "max_zoom": 19, "resolution_m": 0.29, "coverage": [8.78, 46.36, 17.5, 49.04], "regions": ["austria"],
        "attribution": "Datenquelle: basemap.at, CC BY 4.0",
        "licence": "CC BY 4.0 (Open Government Data Österreich)", "licence_url": "https://basemap.at/en/#lizenz",
    },
    "luxembourg": {
        "name": "Luxembourg – geoportail.lu orthophoto",
        "url": "https://wmts1.geoportail.lu/opendata/wmts/ortho_latest/GLOBAL_WEBMERCATOR_4_V3/{z}/{x}/{y}.jpeg",
        "max_zoom": 20, "resolution_m": 0.1, "coverage": [5.72, 49.44, 6.55, 50.19], "regions": ["luxembourg"],
        "attribution": "Orthophoto © ACT Luxembourg (geoportail.lu), CC0",
        "licence": "CC0", "licence_url": "https://data.public.lu/fr/datasets/bd-l-ortho-webservices-wms-et-wmts/",
    },
    "nrw": {
        "name": "Germany, North Rhine-Westphalia – DOP",
        "url": _wms13("https://www.wms.nrw.de/geobasis/wms_nw_dop", "nw_dop_rgb"),
        "max_zoom": 20, "resolution_m": 0.1, "coverage": [5.59, 50.05, 9.75, 52.8], "regions": ["nordrhein-westfalen"],
        "attribution": "© GeoBasis-DE / Bezirksregierung Köln, dl-de/zero-2-0",
        "licence": "Datenlizenz Deutschland Zero 2.0", "licence_url": DL_ZERO,
    },
    "bayern": {
        "name": "Germany, Bavaria – DOP20",
        "url": _wms13("https://geoservices.bayern.de/od/wms/dop/v1/dop20", "by_dop20c"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [8.97, 47.27, 13.84, 50.57], "regions": ["bayern"],
        "attribution": "Bayerische Vermessungsverwaltung – www.geodaten.bayern.de, CC BY 4.0",
        "licence": "CC BY 4.0", "licence_url": "https://www.geodaten.bayern.de/odd/m/3/html/nutzungsbedingungen.html",
    },
    "berlin": {
        "name": "Germany, Berlin – TrueDOP 2025",
        "url": _wms13("https://gdi.berlin.de/services/wms/truedop_2025_sommer", "truedop_2025_sommer_rgb"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [13.04, 52.31, 13.77, 52.69], "regions": ["berlin"],
        "attribution": "© Geoportal Berlin, dl-de/zero-2-0",
        "licence": "Datenlizenz Deutschland Zero 2.0", "licence_url": DL_ZERO,
    },
    "brandenburg": {
        "name": "Germany, Brandenburg – DOP20",
        "url": _wms13("https://isk.geobasis-bb.de/mapproxy/dop20c/service/wms", "bebb_dop20c"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [11.22, 51.3, 14.77, 53.58], "regions": ["brandenburg"],
        "attribution": "© GeoBasis-DE/LGB, dl-de/by-2-0 (Daten geändert)",
        "licence": "Datenlizenz Deutschland Namensnennung 2.0", "licence_url": DL_BY,
    },
    "sachsen": {
        "name": "Germany, Saxony – DOP20",
        "url": _wms13("https://geodienste.sachsen.de/wms_geosn_dop-rgb/guest", "sn_dop_020"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [11.78, 50.15, 15.09, 51.73], "regions": ["sachsen"],
        "attribution": "Quelle: GeoSN, dl-de/by-2-0",
        "licence": "Datenlizenz Deutschland Namensnennung 2.0",
        "licence_url": "https://www.landesvermessung.sachsen.de/allgemeine-nutzungsbedingungen-8954.html",
    },
    "thueringen": {
        "name": "Germany, Thuringia – DOP20",
        "url": _wms13("https://www.geoproxy.geoportal-th.de/geoproxy/services/DOP", "th_dop"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [9.85, 50.15, 12.72, 51.67], "regions": ["thueringen"],
        "attribution": "© GDI-Th, CC BY 4.0",
        "licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    },
    "hessen": {
        "name": "Germany, Hesse – DOP20",
        "url": _wms13("https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows", "he_dop_rgb"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [7.41, 49.25, 10.5, 51.77], "regions": ["hessen"],
        "attribution": "© HVBG Hessen (Daten verändert)",
        "licence": "Open data under § 18 HVGG (any use, no conditions)",
        "licence_url": "https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows?SERVICE=WMS&REQUEST=GetCapabilities",
    },
    "niedersachsen": {
        "name": "Germany, Lower Saxony – DOP20",
        "url": _wms13("https://opendata.lgln.niedersachsen.de/doorman/noauth/dop_wms", "ni_dop20"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [6.5, 51.15, 11.76, 54.15], "regions": ["niedersachsen"],
        "exclude": [[8.48, 53.01, 8.99, 53.23], [8.48, 53.5, 8.65, 53.61]],     # Bremen, Bremerhaven
        "attribution": "LGLN (2026) CC BY 4.0",
        "licence": "CC BY 4.0", "licence_url": "https://creativecommons.org/licenses/by/4.0/",
    },
    "bw": {
        "name": "Germany, Baden-Württemberg – DOP20",
        "url": _wms13("https://owsproxy.lgl-bw.de/owsproxy/ows/WMS_LGL-BW_ATKIS_DOP_20_C", "IMAGES_DOP_20_RGB"),
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [7.5, 47.53, 10.5, 49.8], "regions": ["baden-wuerttemberg"],
        "attribution": "LGL-BW (2026) Datenlizenz Deutschland - Namensnennung - Version 2.0, www.lgl-bw.de",
        "licence": "Datenlizenz Deutschland Namensnennung 2.0", "licence_url": DL_BY,
    },
    "cuzk": {
        "name": "Czech Republic – ČÚZK Ortofoto",
        "url": "https://ags.cuzk.gov.cz/arcgis1/rest/services/ORTOFOTO_WM/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 20, "resolution_m": 0.125, "coverage": [12.09, 48.55, 18.87, 51.06], "regions": ["czech-republic"],
        "attribution": "Ortofoto © ČÚZK (ČÚZK – on-line), CC BY 4.0, upraveno",
        "licence": "CC BY 4.0",
        "licence_url": "https://cuzk.gov.cz/Predpisy/Podminky-poskytovani-prostor-dat-a-sitovych-sluzeb.aspx",
    },
    "gurs": {
        "name": "Slovenia – GURS DOF025",
        "url": _wms13("https://ipi.eprostor.gov.si/wms-si-gurs-dts/wms", "SI.GURS.ZPDZ:DOF025_3857"),
        "max_zoom": 19, "resolution_m": 0.25, "coverage": [13.35, 45.4, 16.62, 46.89], "regions": ["slovenia"],
        "attribution": "Surveying and Mapping Authority of the Republic of Slovenia (GURS), DOF025, CC BY 4.0",
        "licence": "CC BY 4.0", "licence_url": "https://www.e-prostor.gov.si/en/access-to-geodetic-data/",
    },
    "gsi": {
        "name": "Japan – GSI seamless photo",
        "url": "https://cyberjapandata.gsi.go.jp/xyz/seamlessphoto/{z}/{x}/{y}.jpg",
        "max_zoom": 18, "resolution_m": 0.5, "coverage": [122.9, 24.0, 154.0, 45.6], "regions": ["japan"],
        "attribution": "地理院タイル (GSI Japan) を加工して作成, PDL 1.0",
        "licence": "Public Data License 1.0 (CC BY 4.0 compatible)",
        "licence_url": "https://maps.gsi.go.jp/development/ichiran.html",
    },
    "act": {
        "name": "Australia, ACT – ACT Government aerial imagery",
        "url": "https://tiles.arcgis.com/tiles/E5n4f1VY84i0xSjy/arcgis/rest/services/"
               "ACT_Aerial_Imagery_Current_3857/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 21, "resolution_m": 0.075, "coverage": [148.74, -35.93, 149.43, -35.11],
        "attribution": "Imagery CC BY 4.0 Australian Capital Territory and Aerometrex",
        "licence": "CC BY 4.0",
        "licence_url": "https://actmapi-actgov.opendata.arcgis.com/maps/ACTGOV::act-aerial-imagery-current/about",
    },
    "massgis": {
        "name": "United States, Massachusetts – MassGIS 2025",
        "url": "https://tiles.arcgis.com/tiles/hGdibHYSPO59RG1h/arcgis/rest/services/"
               "Massachusetts_Aerial_Imagery_2025/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 20, "resolution_m": 0.15, "coverage": [-73.51, 40.88, -68.73, 42.89], "regions": ["us/massachusetts"],
        "attribution": "Imagery: MassGIS 2025",
        "licence": "No restrictions (MassGIS)",
        "licence_url": "https://www.arcgis.com/home/item.html?id=a3bbd504c80746ac9aedc7b5ca4e0d00",
    },
    "kyfromabove": {
        "name": "United States, Kentucky – KyFromAbove 3-inch",
        "url": "https://kygisserver.ky.gov/arcgis/rest/services/WGS84WM_Services/"
               "Ky_Imagery_Phase3_3IN_WGS84WM/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 21, "resolution_m": 0.075, "coverage": [-89.58, 36.49, -81.96, 39.15], "regions": ["us/kentucky"],
        "attribution": "Imagery: KyFromAbove, Kentucky Division of Geographic Information",
        "licence": "Public domain with attribution",
        "licence_url": "https://registry.opendata.aws/kyfromabove/",
    },
    "nconemap": {
        "name": "United States, North Carolina – NC OneMap orthoimagery",
        "url": "https://services.nconemap.gov/secure/rest/services/Imagery/Orthoimagery_Latest_cached/ImageServer/tile/{z}/{y}/{x}",
        "max_zoom": 20, "resolution_m": 0.15, "coverage": [-84.32, 33.13, -75.4, 36.59], "regions": ["us/north-carolina"],
        "attribution": "Imagery: NC OneMap, NC Center for Geographic Information and Analysis, NC 911 Board",
        "licence": "Free and unrestricted use (NC OneMap)",
        "licence_url": "https://www.nconemap.gov/pages/terms",
    },
    "cteco": {
        "name": "United States, Connecticut – CT ECO 2023",
        "url": "https://cteco.uconn.edu/ctraster/rest/services/images/Ortho_2023_tiled/ImageServer/tile/{z}/{y}/{x}",
        "max_zoom": 21, "resolution_m": 0.075, "coverage": [-73.73, 40.96, -71.79, 42.05], "regions": ["us/connecticut"],
        "attribution": "Imagery: CT ECO, State of Connecticut, 2023",
        "licence": "No use limitations (CT ECO metadata)",
        "licence_url": "https://cteco.uconn.edu/download/metadata/CT_ortho_2023_metadata.htm",
    },
    "indiana": {
        "name": "United States, Indiana – IndianaMap current imagery",
        "url": "https://di-ingov.img.arcgis.com/arcgis/rest/services/CacheWebMercator/"
               "IndianaCurrentImageryCacheL19v4/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 19, "resolution_m": 0.15, "coverage": [-88.1, 37.77, -84.78, 41.76], "regions": ["us/indiana"],
        "attribution": "Imagery: Indiana Geographic Information Office (CC0)",
        "licence": "CC0",
        "licence_url": "https://registry.opendata.aws/in-imagery/",
    },
    "vcgi": {
        "name": "United States, Vermont – VCGI best of colour",
        "url": "https://maps.vcgi.vermont.gov/arcgis/rest/services/EGC_services/IMG_VCGI_CLR_WM_CACHE/ImageServer/tile/{z}/{y}/{x}",
        "max_zoom": 19, "resolution_m": 0.15, "coverage": [-73.44, 42.72, -71.46, 45.02], "regions": ["us/vermont"],
        "attribution": "Imagery: VCGI, Vermont Imagery Program",
        "licence": "Unrestricted use (Vermont Imagery Program)",
        "licence_url": "https://vcgi.vermont.gov/node/156",
    },
    "dc": {
        "name": "United States, Washington DC – OCTO 2025",
        "url": "https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Ortho2025_WebMercator/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 21, "resolution_m": 0.075, "coverage": [-77.12, 38.79, -76.91, 39.0], "regions": ["us/district-of-columbia"],
        "attribution": "Imagery: Office of the Chief Technology Officer, District of Columbia, CC BY 4.0",
        "licence": "CC BY 4.0",
        "licence_url": "https://imagery.dcgis.dc.gov/dcgis/rest/services/Ortho/Ortho_2025/ImageServer/info/metadata",
    },
    "msdis": {
        "name": "United States, Missouri – MSDIS 6-inch 2023–2024",
        "url": "https://stateimagery.msdis.missouri.edu/arcgis/rest/services/Missouri_6inch_Statewide_2023_2024_Dynamic/"
               "ImageServer/exportImage?bbox={bbox}&bboxSR=3857&imageSR=3857&size=256,256&format=jpg&f=image",
        "max_zoom": 20, "resolution_m": 0.15, "coverage": [-95.78, 35.99, -89.09, 40.62], "regions": ["us/missouri"],
        "attribution": "Imagery: Missouri Spatial Data Information Service (MSDIS), CC BY 4.0",
        "licence": "CC BY 4.0",
        "licence_url": "https://registry.opendata.aws/mo-gis-imagery/",
    },
    "ontario": {
        "name": "Canada, Ontario – Ontario Imagery Web Map Service",
        "url": "https://ws.lioservices.lrc.gov.on.ca/arcgis2/rest/services/LIO_Imagery/"
               "Ontario_Imagery_Web_Map_Service/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 19, "resolution_m": 0.2, "coverage": [-95.16, 41.66, -74.31, 57.51], "regions": ["ontario"],
        "attribution": "Contains information licensed under the Open Government Licence – Ontario",
        "licence": "Open Government Licence – Ontario",
        "licence_url": "https://www.ontario.ca/page/open-government-licence-ontario",
    },
    "toronto": {
        "name": "Canada, Toronto – City of Toronto orthophoto",
        "url": "https://gis.toronto.ca/arcgis/rest/services/basemap/cot_ortho/MapServer/tile/{z}/{y}/{x}",
        "max_zoom": 20, "resolution_m": 0.08, "coverage": [-79.65, 43.57, -79.1, 43.86], "outline": "toronto",
        "attribution": "Contains information licensed under the Open Government Licence – Toronto",
        "licence": "Open Government Licence – Toronto",
        "licence_url": "https://open.toronto.ca/open-data-license/",
    },
    "rio": {
        "name": "Brazil, Rio de Janeiro – IPP true orthophoto 2024",
        "url": _wms13("https://pgeo3.rio.rj.gov.br/arcgis/services/Imagens/Mosaico_2024/ImageServer/WMSServer",
                      "Mosaico_2024"),
        "max_zoom": 20, "resolution_m": 0.15, "coverage": [-43.8, -23.09, -43.09, -22.74], "outline": "rio",
        "attribution": "Mosaico 2024, Instituto Pereira Passos, PCRJ, CC BY 4.0",
        "licence": "CC BY 4.0",
        "licence_url": "https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Imagens/Mosaico_2024/ImageServer/info/iteminfo",
    },
    "s2cloudless": {
        "name": "World – Sentinel-2 cloudless 2016",
        "url": "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{y}/{x}.jpg",
        "max_zoom": 14, "resolution_m": 10, "coverage": [-180, -85, 180, 85],
        "attribution": "Sentinel-2 cloudless 2016 by EOX IT Services GmbH "
                       "(contains modified Copernicus Sentinel data 2016), CC BY 4.0",
        "licence": "CC BY 4.0 (2016 layer only; later years are CC BY-NC-SA)", "licence_url": "https://cloudless.eox.at",
    },
}
GROUPS = {"Worldwide": ["s2cloudless"], "Asia": ["gsi"], "North America": ["naip", "ontario", "toronto", "massgis", "kyfromabove", "nconemap", "cteco",
                            "indiana", "vcgi", "dc", "msdis"],
          "South America": ["rio"], "Oceania": ["act"]}
for _k, _v in BUILTIN.items():
    _v.update(id=_k, licence_confirmed=True, builtin=True,
              group=next((g for g, ids in GROUPS.items() if _k in ids), "Europe"))


# user sources live in data/imagery.json
_URL_RE = re.compile(r"^https?://[^\s]+$")


def validate_url(url):
    """Raise ValueError unless url is an XYZ/TMS template ({z} {x} {y} or {-y}) or a {bbox} URL."""
    if not _URL_RE.match(url or ""):
        raise ValueError("imagery URL must start with http:// or https://")
    if "{bbox}" in url:          # WMS / ArcGIS exportImage: EPSG:3857 minx,miny,maxx,maxy
        return
    for k in ("{z}", "{x}"):
        if k not in url:
            raise ValueError(f"imagery URL needs {k} (or use {{bbox}} for WMS-style URLs)")
    if "{y}" not in url and "{-y}" not in url:
        raise ValueError("imagery URL needs {y} (or {-y} for TMS)")


def _store(data_dir: Path) -> Path:
    return data_dir / "imagery.json"


def _user_sources(data_dir: Path) -> dict:
    p = _store(data_dir)
    return json.loads(p.read_text()) if p.exists() else {}


_STORE_LOCK = threading.Lock()


def _write_store(data_dir: Path, user: dict):
    _write_atomic(_store(data_dir), json.dumps(user, indent=1).encode())


def source_id(name: str) -> str:
    """Id of a user source, from its name."""
    sid = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "imagery"
    return sid + "-custom" if sid in BUILTIN else sid


def list_sources(data_dir: Path) -> dict:
    """{id: config} of built-in and user sources."""
    return {**BUILTIN, **_user_sources(data_dir)}


def get_source(data_dir: Path, sid) -> dict:
    s = list_sources(data_dir).get(sid)
    if s is None:
        raise KeyError(f"unknown imagery source: {sid}")
    return s


def _check_coverage(cov):
    if cov is None:
        return None
    try:
        w, s, e, n = (float(v) for v in cov)
    except (TypeError, ValueError):
        raise ValueError("coverage must be [min_lon, min_lat, max_lon, max_lat]")
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError("coverage must be [min_lon, min_lat, max_lon, max_lat] in degrees")
    return [round(v, 5) for v in (w, s, e, n)]


def geotiff_root(data_dir: Path) -> Path:
    """Folder the user copies GeoTIFF folders into: tak-buildings3d-data/geotiff/<name>/."""
    return data_dir.parent / "geotiff"


def save_source(data_dir: Path, name, url, max_zoom, attribution, licence_confirmed, coverage=None, folder=None):
    """Register a user imagery source (a tile URL or a GeoTIFF folder); the user must confirm its licence."""
    if not licence_confirmed:
        raise ValueError("confirm that this imagery's licence allows derived 3D models")
    if not attribution.strip():
        raise ValueError("attribution text is required")
    sid = source_id(name)
    if folder:
        from . import geotiff
        root = geotiff_root(data_dir)
        if folder not in geotiff.folders(root):
            raise ValueError(f"no GeoTIFF folder called {folder!r} in {root.name}/")
        info = geotiff.scan(root / folder)
        lat = (info["coverage"][1] + info["coverage"][3]) / 2
        extra = {"backend": "geotiff", "folder": folder, "url": f"/api/imagery/{sid}/tiles/{{z}}/{{x}}/{{y}}",
                 "min_zoom": geotiff.MIN_ZOOM, "max_zoom": geotiff.max_zoom(info["resolution_m"], lat), **info}
    else:
        validate_url(url)
        extra = {"url": url.strip(), "max_zoom": int(max_zoom), "coverage": _check_coverage(coverage)}
    with _STORE_LOCK:
        user = _user_sources(data_dir)
        if sid in user:                 # saved again: its old tiles may be out of date
            clear_cache(data_dir, sid)
        _FOOTPRINTS.pop(sid, None)
        user[sid] = {"id": sid, "name": name.strip(), **extra,
                     "attribution": attribution.strip(), "licence_confirmed": True,
                     "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        _write_store(data_dir, user)
    return user[sid]


def delete_source(data_dir: Path, sid):
    """Remove a user source and its cached tiles."""
    with _STORE_LOCK:
        user = _user_sources(data_dir)
        if sid in user:
            clear_cache(data_dir, sid)
        user.pop(sid, None)
        _FOOTPRINTS.pop(sid, None)
        _write_store(data_dir, user)


def coverage(src):
    """[min_lon, min_lat, max_lon, max_lat]; user sources saved without one count as worldwide."""
    return src.get("coverage") or WORLD


# Coverage boxes overlap at borders (France's reaches Barcelona), so a built-in source can name
# Geofabrik regions whose outlines, clipped to the box and minus "exclude" boxes, are its footprint.
# Without the Geofabrik index (offline) the box is used. City sources instead name an "outline" in
# outlines.json: the city boundary from OpenStreetMap, simplified.
_FOOTPRINTS = {}
_FOOTPRINT_LOCK = threading.Lock()


def footprint(src):
    """(geometry, prepared geometry) where the source has imagery, or None for its coverage box."""
    if not src.get("regions") and not src.get("outline") and not src.get("files"):
        return None
    with _FOOTPRINT_LOCK:
        if src["id"] not in _FOOTPRINTS and src.get("files"):
            g = unary_union([box(*f["bounds"]) for f in src["files"]])
            _FOOTPRINTS[src["id"]] = (g, prep(g))
        if src["id"] not in _FOOTPRINTS and src.get("outline"):
            g = shape(json.loads((Path(__file__).parent / "outlines.json").read_text())[src["outline"]])
            _FOOTPRINTS[src["id"]] = (g, prep(g))
        if src["id"] not in _FOOTPRINTS:
            try:
                from . import regions
                if not (regions.DATA / "geofabrik-index-v1.json").exists():
                    return None                  # don't download the index just for this
                parts = [g for p, g, _ in regions._shapes() if p["id"] in src["regions"]]
            except Exception:
                return None
            g = unary_union(parts).intersection(box(*coverage(src))) if parts else None
            for ex in src.get("exclude", []):
                g = g.difference(box(*ex)) if g is not None else None
            _FOOTPRINTS[src["id"]] = (g, prep(g)) if g is not None and not g.is_empty else None
        return _FOOTPRINTS[src["id"]]


_CONTINENTS = {"Australia and Oceania": "Oceania", "Russian Federation": "Europe"}
_PLACES = {}


def place(src):
    """{"group": continent, "country": name} for a user source, from the Geofabrik outlines its
    coverage box overlaps most. Empty without the index; "Worldwide" without a coverage box."""
    if not src.get("coverage"):
        return {"group": "Worldwide"}
    key = tuple(src["coverage"])
    if key not in _PLACES:
        try:
            from . import regions
            if not (regions.DATA / "geofabrik-index-v1.json").exists():
                return {}
            shapes = regions._shapes()
        except Exception:
            return {}
        parent = {p["id"]: p.get("parent") for p, _, _ in shapes}
        name = {p["id"]: p["name"] for p, _, _ in shapes}
        b = box(*key)
        cands = []                 # countries: children of a continent, or Russia's districts
        for p, g, _ in shapes:
            par = parent.get(p["id"])
            if par is not None and (parent.get(par) is None or par == "russia") and g.intersects(b):
                cands.append(("russia" if par == "russia" else p["id"], g))
        # skip groupings like "Alps" or "DACH" that contain another candidate country
        cands = [(r, g) for r, g in cands
                 if not any(h.area < g.area and g.intersection(h).area > h.area / 2 for _, h in cands)]
        best = max(((g.intersection(b).area, r) for r, g in cands), default=None)
        if best is None:
            _PLACES[key] = {}
        else:
            rid = best[1]
            top = parent.get(rid) or rid
            _PLACES[key] = {"group": _CONTINENTS.get(name[top], name[top]),
                            "country": "Russia" if rid == "russia" else name[rid]}
    return _PLACES[key]


def footprint_geojson(src):
    """Footprint simplified to ~1 km for the map, as GeoJSON, or None."""
    fp = footprint(src)
    if fp is None:
        return None
    g = fp[0].simplify(0.01, preserve_topology=True)
    return {"geometry": mapping(g), "area": round(g.area, 4)}


def covers(src, geom):
    """True if the source's coverage overlaps a shapely geometry."""
    w, s, e, n = coverage(src)
    gw, gs, ge, gn = geom.bounds
    if not (gw <= e and ge >= w and gs <= n and gn >= s):
        return False
    fp = footprint(src)
    return fp is None or fp[1].intersects(geom)


def contains(src, x, y):
    """True if the source has imagery at lon x, lat y."""
    w, s, e, n = coverage(src)
    if not (w <= x <= e and s <= y <= n):
        return False
    fp = footprint(src)
    return fp is None or fp[1].contains(Point(x, y))


def _size(src):
    """Area in square degrees: of all sources covering a point, the smallest is the most local."""
    fp = footprint(src)
    if fp is not None:
        return fp[0].area
    w, s, e, n = coverage(src)
    return (e - w) * (n - s)


def available(data_dir: Path, geom):
    """Sources (built-in and user) whose coverage overlaps the geometry."""
    return [v for v in list_sources(data_dir).values() if covers(v, geom)]


def native_mpp(src, lat) -> float:
    """Finest useful ground resolution (m/px): the imagery's own, or what its max zoom gives."""
    return max(src.get("resolution_m") or 0.0, Z0_MPP * math.cos(math.radians(lat)) / 2 ** src["max_zoom"])


def default_mpp(src, lat) -> float:
    """Default roof texture m/px: the imagery's own, but not finer than PHOTO_DEFAULT_MPP."""
    return max(round(native_mpp(src, lat), 2), PHOTO_DEFAULT_MPP)


def best_source(data_dir: Path, geom, roofs="colour"):
    """Highest-resolution source covering the area's centroid, or None.

    User sources count only if saved with a coverage box; texture roofs skip coarse imagery."""
    c = geom.centroid
    cands = [v for v in list_sources(data_dir).values() if (v.get("builtin") or v.get("coverage"))
             and (roofs != "texture" or native_mpp(v, c.y) <= TEXTURE_MAX_MPP) and contains(v, c.x, c.y)]
    return max(cands, key=lambda v: (v["max_zoom"], -_size(v))) if cands else None


# tiles are cached in data/tiles/<key>/z/x/y.img, one folder per source URL
def cache_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def source_cache_key(src) -> str:
    """Cache folder name for a source, from its URL or Planetary Computer collection."""
    if src.get("backend") == "planetary_computer":
        return cache_key(f"planetarycomputer:{src['pc_collection']}")
    if src.get("backend") == "geotiff":
        return cache_key(f"geotiff:{src['folder']}")
    return cache_key(src["url"])


def cache_usage(data_dir: Path):
    """[{key, id, name, tiles, bytes}] per cache folder; id/name None for removed sources."""
    root = data_dir / "tiles"
    by_key = {source_cache_key(v): v for v in list_sources(data_dir).values()}
    out = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if not d.is_dir():
            continue
        files = [f for f in d.rglob("*") if f.is_file()]   # map tiles, NAIP chunks, STAC searches
        tiles = sum(f.suffix in (".img", ".png") for f in files)
        src = by_key.get(d.name)
        out.append({"key": d.name, "id": src and src["id"], "name": src and src["name"],
                    "tiles": tiles, "bytes": sum(f.stat().st_size for f in files)})
    return out


def clear_cache(data_dir: Path, key: str | None = None):
    """Delete one source's cached tiles (by cache key or source id), or all of them."""
    root = data_dir / "tiles"
    if key is None:
        shutil.rmtree(root, ignore_errors=True)
        return
    srcs = list_sources(data_dir)
    if key in srcs:
        key = source_cache_key(srcs[key])
    if not re.fullmatch(r"[0-9a-f]{12}", key):       # also keeps "../" out of the path
        raise ValueError("bad cache key")
    shutil.rmtree(root / key, ignore_errors=True)


def lonlat_to_pixel(lon, lat, z):
    """Global Web Mercator pixel coordinates at zoom z."""
    n = TILE * 2 ** z
    x = (lon + 180.0) / 360.0 * n
    s = math.sin(math.radians(lat))
    y = (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n
    return x, y


def zoom_for_mpp(lat, mpp, max_zoom) -> int:
    """Coarsest zoom whose ground resolution is at least as fine as mpp."""
    return max(0, min(max_zoom, math.ceil(math.log2(Z0_MPP * math.cos(math.radians(lat)) / mpp))))


def merc_bounds(z, x, y):
    """(minx, miny, maxx, maxy) of tile z/x/y in EPSG:3857 metres."""
    s = 2 * MERC_R / 2 ** z
    return (-MERC_R + x * s, MERC_R - (y + 1) * s, -MERC_R + (x + 1) * s, MERC_R - y * s)


def merc_to_lonlat(x, y):
    """EPSG:3857 metres -> lon/lat degrees."""
    return x / MERC_R * 180.0, math.degrees(2 * math.atan(math.exp(y / MERC_R * math.pi)) - math.pi / 2)


def open_source(src, cache_dir: Path):
    """TileSource for a source config (or built-in id), using its backend if it has one."""
    if isinstance(src, str):
        src = BUILTIN[src]
    if src.get("backend") == "planetary_computer":
        from .naip_pc import PlanetaryComputerSource   # avoids a circular import
        return PlanetaryComputerSource(src, cache_dir)
    if src.get("backend") == "geotiff":
        from .geotiff import GeoTiffSource
        return GeoTiffSource(src, cache_dir, geotiff_root(cache_dir.parent))
    return TileSource(src, cache_dir)


def fetch_parallel(fn, jobs, unit, log=None, progress=None, workers=PREFETCH_WORKERS) -> int:
    """Run fn(*job) for each job on a thread pool; returns how many raised."""
    done, bad, last = 0, 0, time.time()
    ex = ThreadPoolExecutor(workers)
    try:
        for fut in as_completed([ex.submit(fn, *job) for job in jobs]):
            bad += fut.exception() is not None
            done += 1
            if progress:
                progress(done / len(jobs))
            if log and (time.time() - last > 15 or done == len(jobs)):
                log(f"      imagery: {done}/{len(jobs)} {unit}" + (f" ({bad} failed)" if bad else ""))
                last = time.time()
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
    return bad


_M_PER_DEG_LON = 111320          # at the equator; times cos(lat) elsewhere
_M_PER_DEG_LAT = 110540


def _is_image(data: bytes) -> bool:
    try:
        Image.open(io.BytesIO(data)).verify()
        return True
    except Exception:
        return False


def _write_atomic(p: Path, data: bytes):
    """Write via a temp file, so a reader never sees half a tile."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{threading.get_ident()}.part")
    tmp.write_bytes(data)
    tmp.replace(p)


class TileSource:
    """XYZ / TMS / {bbox} imagery source with an on-disk tile cache; failed tiles come back grey."""

    def __init__(self, cfg, cache_dir: Path):
        if isinstance(cfg, str):
            cfg = BUILTIN[cfg]
        if not cfg.get("licence_confirmed"):
            raise ValueError("imagery source has no licence confirmation")
        self.cfg = cfg
        self.cache = cache_dir / source_cache_key(cfg)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.failed = 0
        self.fetched = 0
        self._lock = threading.Lock()

    def _tile_path(self, z, x, y) -> Path:
        return self.cache / str(z) / str(x) / f"{y}.img"

    def _blank(self):
        with self._lock:
            self.failed += 1
        return Image.new("RGB", (TILE, TILE), (128, 128, 128))

    def tile(self, z, x, y):
        """RGB tile z/x/y, from the cache or downloaded; grey if unavailable."""
        p = self._tile_path(z, x, y)
        if not p.exists():
            bb = ",".join(f"{v:.3f}" for v in merc_bounds(z, x, y))
            url = (self.cfg["url"].replace("{z}", str(z)).replace("{x}", str(x)).replace("{bbox}", bb)
                   .replace("{-y}", str(2 ** z - 1 - y)).replace("{y}", str(y)))
            data = None
            for attempt in range(3):
                try:
                    r = self.session.get(url, timeout=30)
                    if r.status_code == 200 and _is_image(r.content):    # WMS errors come back as 200 XML
                        data = r.content
                        break
                except requests.RequestException:
                    pass
                time.sleep(1 + attempt)
            if data is None:
                return self._blank()
            _write_atomic(p, data)
            with self._lock:
                self.fetched += 1
        try:
            return Image.open(io.BytesIO(p.read_bytes())).convert("RGB")
        except Exception:
            return self._blank()

    def tiles_for(self, bounds, z):
        """{(z, x, y)} covering each (lon_min, lat_min, lon_max, lat_max) box."""
        out = set()
        for lon_min, lat_min, lon_max, lat_max in bounds:
            x0, y0 = lonlat_to_pixel(lon_min, lat_max, z)
            x1, y1 = lonlat_to_pixel(lon_max, lat_min, z)
            for tx in range(int(x0) // TILE, int(math.ceil(x1) - 1) // TILE + 1):
                for ty in range(int(y0) // TILE, int(math.ceil(y1) - 1) // TILE + 1):
                    out.add((z, tx, ty))
        return out

    def prefetch(self, keys, workers=PREFETCH_WORKERS, log=None, progress=None):
        """Download uncached (z, x, y) tiles in parallel; render-on-request servers are slow per tile."""
        todo = sorted(k for k in keys if not self._tile_path(*k).exists())
        if not todo:
            return
        if log:
            log(f"      imagery: fetching {len(todo)} tiles ({len(keys) - len(todo)} cached)")
        fetch_parallel(self.tile, todo, "tiles", log, progress, workers)

    def prefetch_area(self, bounds, mpp, texture, log=None, progress=None):
        """Fetch the imagery roofs in these lon/lat boxes need at mpp; `texture` is for other backends."""
        lat = (min(b[1] for b in bounds) + max(b[3] for b in bounds)) / 2 if bounds else 0.0
        z = zoom_for_mpp(lat, mpp, self.cfg["max_zoom"])
        self.prefetch(self.tiles_for(bounds, z), log=log, progress=progress)

    def crop(self, lon_min, lat_min, lon_max, lat_max, z):
        """Mosaic covering bbox at zoom z. Returns (image, (px0, py0)) in global pixels."""
        x0, y0 = lonlat_to_pixel(lon_min, lat_max, z)
        x1, y1 = lonlat_to_pixel(lon_max, lat_min, z)
        px0, py0, px1, py1 = int(math.floor(x0)), int(math.floor(y0)), int(math.ceil(x1)), int(math.ceil(y1))
        img = Image.new("RGB", (max(1, px1 - px0), max(1, py1 - py0)))
        for tx in range(px0 // TILE, (px1 - 1) // TILE + 1):
            for ty in range(py0 // TILE, (py1 - 1) // TILE + 1):
                img.paste(self.tile(z, tx, ty), (tx * TILE - px0, ty * TILE - py0))
        return img, (px0, py0)

    def roof_image(self, lon_min, lat_min, lon_max, lat_max, mpp):
        """(image at ~mpp m/px covering the box, uv(lon, lat) -> (u, v) into it)."""
        lat = (lat_min + lat_max) / 2
        z = zoom_for_mpp(lat, mpp, self.cfg["max_zoom"])
        img, (px0, py0) = self.crop(lon_min, lat_min, lon_max, lat_max, z)
        Wp, Hp = img.size
        w_m = (lon_max - lon_min) * _M_PER_DEG_LON * math.cos(math.radians(lat))
        h_m = (lat_max - lat_min) * _M_PER_DEG_LAT
        img = img.resize((max(4, math.ceil(w_m / mpp)), max(4, math.ceil(h_m / mpp))), Image.LANCZOS)

        def uv(lon, lat_):
            gx, gy = lonlat_to_pixel(lon, lat_, z)
            return (gx - px0) / Wp, (gy - py0) / Hp
        return img, uv

    def footprint_colour(self, polys_lonlat, mpp=1.0):
        """Median RGB inside the footprint polygons (lon/lat), or the pixel under a tiny roof."""
        lon_min = min(p.bounds[0] for p in polys_lonlat)
        lat_min = min(p.bounds[1] for p in polys_lonlat)
        lon_max = max(p.bounds[2] for p in polys_lonlat)
        lat_max = max(p.bounds[3] for p in polys_lonlat)
        z = zoom_for_mpp((lat_min + lat_max) / 2, mpp, self.cfg["max_zoom"])
        img, (px0, py0) = self.crop(lon_min, lat_min, lon_max, lat_max, z)

        def pixels(ring):
            return [tuple(v - o for v, o in zip(lonlat_to_pixel(x, y, z), (px0, py0))) for x, y in ring.coords]
        mask = Image.new("L", img.size, 0)
        d = ImageDraw.Draw(mask)
        for poly in polys_lonlat:
            d.polygon(pixels(poly.exterior), fill=255, outline=0)   # outline=0 trims ~1 px so walls don't bleed in
            for ring in poly.interiors:
                d.polygon(pixels(ring), fill=0)
        px = np.asarray(img)[np.asarray(mask) > 0]
        if len(px) < 6:          # too few pixels for a median
            pt = max(polys_lonlat, key=lambda p: p.area).representative_point()
            x, y = (v - o for v, o in zip(lonlat_to_pixel(pt.x, pt.y, z), (px0, py0)))
            return img.getpixel((min(max(int(x), 0), img.width - 1), min(max(int(y), 0), img.height - 1)))[:3]
        return tuple(int(v) for v in np.median(px, axis=0))
