# Licences, data sources and attribution

tak-buildings3d doesn't include any map data, imagery or elevation data. It downloads them as you
use it, and the tilesets you build are covered by the licences of that data, summarised below.
This is a practical summary, not legal advice. Terms change, so check each provider's terms
before you publish anything you build.

Every build carries its credits in `ATTRIBUTION.txt` inside the tileset zip, and in the
`attribution` list in `report.json`.

## Source code

Copyright 2026 Tactical Sensor Integration, licensed under the [Apache License 2.0](LICENSE).

## Data used in every build

| Data | Provider | Licence | Notes |
|---|---|---|---|
| Building footprints and tags | © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) | [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/) | The tilesets are a Produced Work. You can share them on any terms, but credit "© OpenStreetMap contributors" wherever they're shown. See below for derived databases |
| OSM extracts (`.osm.pbf`) | [Geofabrik](https://download.geofabrik.de/) | ODbL | Downloaded when needed, not redistributed |
| Small-area OSM queries | Public [Overpass API](https://wiki.openstreetmap.org/wiki/Overpass_API) servers | ODbL, plus the [usage policy](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html) | A shared volunteer service. Overpass builds are limited to 4 km², results are cached, and a busy server gets a 30 s pause before a retry. For commercial use, set `TAK_BUILDINGS3D_OVERPASS_URL` to your own or a paid server |
| Ground heights | Copernicus DEM GLO-30, © DLR e.V. 2010–2014 and © Airbus Defence and Space GmbH 2014–2018, provided under COPERNICUS by the European Union and ESA | [Copernicus DEM licence](https://spacedata.copernicus.eu/collections/copernicus-digital-elevation-model): free, worldwide, with attribution | Read from the AWS open data copy |
| Geoid (MSL to ellipsoid) | EGM2008, US National Geospatial-Intelligence Agency, via the [PROJ data CDN](https://cdn.proj.org/) | Public domain | |

### ODbL: produced works and derived databases

The tilesets are a Produced Work, so you can license them however you like as long as you keep
the OSM credit.

`report.json` lists OSM data for each building (ids, heights, roof shapes, colours), and the
buildings-only `.osm.pbf` files in `tak-buildings3d-data/` are reduced copies of OSM. If you
publish either, it's a Derived Database and has to stay under the ODbL.

Section 4.6 of the ODbL says that anyone publicly using a Produced Work made from a derived
database must also make that database, or the method used to create it, available. tak-buildings3d is
that method and it's open source, so `ATTRIBUTION.txt` names it and links here.

## Imagery

Roof textures and measured roof colours are derived from the imagery, so the imagery's licence
has to allow derived works and redistribution.

### Built-in sources

These are defined in `tak_buildings3d/imagery.py`, each with its licence, attribution text and a link
to its terms. All of them allow commercial use and derived works. Licences were checked on the
providers' own pages and service metadata in November 2026, and each service was tested.

| Source | Coverage | Licence | Credit |
|---|---|---|---|
| EOX Sentinel-2 cloudless 2016 | World | [CC BY 4.0](https://cloudless.eox.at) | Sentinel-2 cloudless 2016 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2016) |
| basemap.at Orthofoto | Austria | [CC BY 4.0](https://basemap.at/en/#lizenz) (Open Government Data Österreich) | Datenquelle: basemap.at |
| ČÚZK Ortofoto | Czech Republic | [CC BY 4.0](https://cuzk.gov.cz/Predpisy/Podminky-poskytovani-prostor-dat-a-sitovych-sluzeb.aspx) | ČÚZK – on-line, with a note that it was modified |
| IGN BD ORTHO (Géoplateforme) | France | [Licence Ouverte / Etalab 2.0](https://cartes.gouv.fr/cgu/) | © IGN |
| Baden-Württemberg DOP20 | Baden-Württemberg | [dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0) | LGL-BW (year), www.lgl-bw.de |
| Bayern DOP20 | Bavaria | [CC BY 4.0](https://www.geodaten.bayern.de/odd/m/3/html/nutzungsbedingungen.html) | Bayerische Vermessungsverwaltung – www.geodaten.bayern.de |
| Berlin TrueDOP 2025 | Berlin | [dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0) | © Geoportal Berlin (courtesy) |
| Brandenburg DOP20 | Brandenburg | [dl-de/by-2-0](https://www.govdata.de/dl-de/by-2-0) | © GeoBasis-DE/LGB (Daten geändert) |
| Hessen DOP20 | Hesse | Open data under § 18 HVGG: any use, commercial included, no conditions (service metadata) | © HVBG Hessen, Daten verändert (courtesy) |
| Niedersachsen DOP20 | Lower Saxony (not Bremen) | CC BY 4.0 (service metadata) | LGLN (year), CC BY 4.0 |
| Geobasis NRW DOP | North Rhine-Westphalia | [dl-de/zero-2-0](https://www.govdata.de/dl-de/zero-2-0) | © GeoBasis-DE / Bezirksregierung Köln (courtesy) |
| Sachsen DOP20 | Saxony | [dl-de/by-2-0](https://www.landesvermessung.sachsen.de/allgemeine-nutzungsbedingungen-8954.html) | Quelle: GeoSN |
| Thüringen DOP20 | Thuringia | CC BY 4.0 (service metadata) | © GDI-Th |
| geoportail.lu orthophoto | Luxembourg | [CC0](https://data.public.lu/fr/datasets/bd-l-ortho-webservices-wms-et-wmts/) | ACT Luxembourg (courtesy) |
| PDOK Luchtfoto (Beeldmateriaal Nederland) | Netherlands | [CC BY 4.0](https://www.beeldmateriaal.nl/data-room) | Beeldmateriaal Nederland / PDOK |
| DGT Ortofotos 2025 | Mainland Portugal | [CC BY 4.0](https://snig.dgterritorio.gov.pt/rndg/srv/api/records/96d83932-f957-40d6-a2ea-3a25751c4b2d) | Informação geográfica cedida pela Direção-Geral do Território |
| GURS DOF025 | Slovenia | [CC BY 4.0](https://www.e-prostor.gov.si/en/access-to-geodetic-data/) | Surveying and Mapping Authority of the Republic of Slovenia |
| IGN España PNOA | Spain, Balearic and Canary Islands (not Melilla) | [CC BY 4.0](https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf) | Obra derivada de PNOA CC BY 4.0 scne.es |
| swisstopo SWISSIMAGE | Switzerland | [swisstopo free geodata](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices), with attribution | © swisstopo |
| GSI seamless photo (地理院タイル) | Japan | [Public Data License 1.0](https://maps.gsi.go.jp/development/ichiran.html), compatible with CC BY 4.0 | 地理院タイル (GSI), edited |
| USDA NAIP, read from [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/naip) (original 0.6 m GeoTIFFs), with the USGS National Map service as map layer and fallback | Contiguous US | Public domain ([USGS](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits)). Planetary Computer's [terms of use](https://planetarycomputer.microsoft.com/terms) apply; no account needed | Imagery: USDA NAIP (public domain), via Microsoft Planetary Computer and USGS The National Map |
| CT ECO 2023 orthoimagery (7.5 cm) | Connecticut | No use limitations ([metadata](https://cteco.uconn.edu/download/metadata/CT_ortho_2023_metadata.htm)) | CT ECO, State of Connecticut |
| IndianaMap current imagery | Indiana | [CC0](https://registry.opendata.aws/in-imagery/) | Indiana Geographic Information Office |
| KyFromAbove 3-inch | Kentucky | Public domain with attribution ([registry](https://registry.opendata.aws/kyfromabove/)) | KyFromAbove, Kentucky Division of Geographic Information |
| MassGIS 2025 aerial imagery | Massachusetts | "No restrictions apply to these data" ([item](https://www.arcgis.com/home/item.html?id=a3bbd504c80746ac9aedc7b5ca4e0d00)) | MassGIS |
| MSDIS 6-inch 2023–2024 | Missouri | [CC BY 4.0](https://registry.opendata.aws/mo-gis-imagery/) | Missouri Spatial Data Information Service |
| NC OneMap orthoimagery (latest) | North Carolina | Free and unrestricted use ([terms](https://www.nconemap.gov/pages/terms)) | NC Center for Geographic Information and Analysis, NC 911 Board |
| VCGI best of colour | Vermont | Free and unrestricted in use ([Vermont Imagery Program](https://vcgi.vermont.gov/node/156)) | VCGI, Vermont Imagery Program |
| OCTO 2025 orthoimagery (7.5 cm) | Washington DC | [CC BY 4.0](https://imagery.dcgis.dc.gov/dcgis/rest/services/Ortho/Ortho_2025/ImageServer/info/metadata) | Office of the Chief Technology Officer, District of Columbia |
| Ontario Imagery Web Map Service (OIWMS) | Ontario | [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario) | Contains information licensed under the Open Government Licence – Ontario |
| City of Toronto orthophoto | City of Toronto | [Open Government Licence – Toronto](https://open.toronto.ca/open-data-license/) | Contains information licensed under the Open Government Licence – Toronto |
| IPP true orthophoto 2024 (Mosaico 2024) | City of Rio de Janeiro | [CC BY 4.0](https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Imagens/Mosaico_2024/ImageServer/info/iteminfo) | Instituto Pereira Passos, PCRJ |
| ACT Government aerial imagery | Canberra (ACT) | [CC BY 4.0](https://actmapi-actgov.opendata.arcgis.com/maps/ACTGOV::act-aerial-imagery-current/about) | Australian Capital Territory and Aerometrex |

Toronto's and Rio's sources cover only the city, so they're used inside the city boundary from
OpenStreetMap, kept simplified in `tak_buildings3d/outlines.json` (© OpenStreetMap contributors, ODbL).

Only the 2016 Sentinel-2 cloudless mosaic is CC BY 4.0. EOX's later mosaics are CC BY-NC-SA,
which rules out commercial use, so the built-in source stays on 2016.

### Sources you add

You can add any imagery service under Settings, or your own GeoTIFFs: copy a folder of them into
`tak-buildings3d-data/geotiff/` and pick it under Add imagery. MrSID and ECW files need converting
first, for example with QGIS (Raster → Conversion → Translate). Before it can be saved you have to give its
attribution text and tick a box confirming that its licence allows 3D models to be made from it
and shared. The confirmation, its date and the attribution are saved in
`tak-buildings3d-data/data/imagery.json` and in the report of each build that uses it. Checking the
licence is up to you.

## Map display in the web app

These are only used to show the map in the browser. None of them end up in a build.

| Item | Licence or policy |
|---|---|
| OpenStreetMap standard tiles (base map) | © OpenStreetMap contributors, under the [tile usage policy](https://operations.osmfoundation.org/policies/tiles/). Fine for browsing in a local tool, but don't bulk-download or pre-seed tiles |
| Imagery layers | Shown with each source's own credit, the same as in builds |
| Terrain | Copernicus DEM GLO-30, served by the app's own server from the AWS open data copy, with the same credit as the building heights. No Cesium ion data |
| [CesiumJS](https://github.com/CesiumGS/cesium) 1.147, from jsDelivr | Apache-2.0. No Cesium ion token is used |
| [mgrs](https://github.com/proj4js/mgrs) 2.2.0, from jsDelivr | MIT |
| Place search with [OSM Nominatim](https://nominatim.org/) | ODbL data, under the [usage policy](https://operations.osmfoundation.org/policies/nominatim/). A search is only sent when you press Enter, one at a time |

## Software dependencies

pip installs the Python dependencies, all under permissive licences (BSD, MIT, ISC, Apache-2.0).
The Docker image also includes osmium-tool (GPL-3.0+), which tak-buildings3d runs as a separate
program without linking to it.
