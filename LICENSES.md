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

These are listed in `tak_buildings3d/sources.json`, each with its licence, a link to the provider's
terms, the attribution text and the date it was checked. All of them allow commercial use and
derived works. Licences were checked on the providers' own pages and service metadata, and each
service was tested. The table below is generated from that file with
`python -m tak_buildings3d.licences`, and CI fails if it falls out of step.

<!-- sources.json table -->
| Source | Licence | Attribution |
|---|---|---|
| World – Sentinel-2 cloudless 2016 | [CC BY 4.0 (2016 layer only; later years are CC BY-NC-SA)](https://cloudless.eox.at) | Sentinel-2 cloudless 2016 by EOX IT Services GmbH (contains modified Copernicus Sentinel data 2016), CC BY 4.0 |
| Austria – basemap.at Orthofoto | [CC BY 4.0 (Open Government Data Österreich)](https://basemap.at/en/#lizenz) | Datenquelle: basemap.at, CC BY 4.0 |
| Czech Republic – ČÚZK Ortofoto | [CC BY 4.0](https://cuzk.gov.cz/Predpisy/Podminky-poskytovani-prostor-dat-a-sitovych-sluzeb.aspx) | Ortofoto © ČÚZK (ČÚZK – on-line), CC BY 4.0, upraveno |
| France – IGN BD ORTHO | [Etalab Open Licence 2.0](https://cartes.gouv.fr/cgu/) | Imagery (c) IGN BD ORTHO, Licence Ouverte / Etalab 2.0 |
| Germany, Baden-Württemberg – DOP20 | [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0) | LGL-BW (2026) Datenlizenz Deutschland - Namensnennung - Version 2.0, www.lgl-bw.de |
| Germany, Bavaria – DOP20 | [CC BY 4.0](https://www.geodaten.bayern.de/odd/m/3/html/nutzungsbedingungen.html) | Bayerische Vermessungsverwaltung – www.geodaten.bayern.de, CC BY 4.0 |
| Germany, Berlin – TrueDOP 2025 | [Datenlizenz Deutschland Zero 2.0](https://www.govdata.de/dl-de/zero-2-0) | © Geoportal Berlin, dl-de/zero-2-0 |
| Germany, Brandenburg – DOP20 | [Datenlizenz Deutschland Namensnennung 2.0](https://www.govdata.de/dl-de/by-2-0) | © GeoBasis-DE/LGB, dl-de/by-2-0 (Daten geändert) |
| Germany, Hesse – DOP20 | [Open data under § 18 HVGG (any use, no conditions)](https://www.gds-srv.hessen.de/cgi-bin/lika-services/ogc-free-images.ows?SERVICE=WMS&REQUEST=GetCapabilities) | © HVBG Hessen (Daten verändert) |
| Germany, Lower Saxony – DOP20 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | LGLN (2026) CC BY 4.0 |
| Germany, North Rhine-Westphalia – DOP | [Datenlizenz Deutschland Zero 2.0](https://www.govdata.de/dl-de/zero-2-0) | © GeoBasis-DE / Bezirksregierung Köln, dl-de/zero-2-0 |
| Germany, Saxony – DOP20 | [Datenlizenz Deutschland Namensnennung 2.0](https://www.landesvermessung.sachsen.de/allgemeine-nutzungsbedingungen-8954.html) | Quelle: GeoSN, dl-de/by-2-0 |
| Germany, Thuringia – DOP20 | [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) | © GDI-Th, CC BY 4.0 |
| Luxembourg – geoportail.lu orthophoto | [CC0](https://data.public.lu/fr/datasets/bd-l-ortho-webservices-wms-et-wmts/) | Orthophoto © ACT Luxembourg (geoportail.lu), CC0 |
| Netherlands – PDOK Luchtfoto | [CC BY 4.0](https://www.beeldmateriaal.nl/data-room) | Luchtfoto: Beeldmateriaal Nederland / PDOK, CC BY 4.0 |
| Portugal – DGT Ortofotos 2025 | [CC BY 4.0](https://snig.dgterritorio.gov.pt/rndg/srv/api/records/96d83932-f957-40d6-a2ea-3a25751c4b2d) | Informação geográfica cedida pela Direção-Geral do Território (Ortofotos 2025), CC BY 4.0 |
| Slovenia – GURS DOF025 | [CC BY 4.0](https://www.e-prostor.gov.si/en/access-to-geodetic-data/) | Surveying and Mapping Authority of the Republic of Slovenia (GURS), DOF025, CC BY 4.0 |
| Spain – IGN PNOA | [CC BY 4.0](https://www.ign.es/resources/licencia/Condiciones_licenciaUso_IGN.pdf) | Obra derivada de PNOA CC BY 4.0 scne.es |
| Switzerland – swisstopo SWISSIMAGE | [swisstopo OGD (free use, attribution)](https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices) | Imagery (c) swisstopo (SWISSIMAGE), free use with attribution |
| Japan – GSI seamless photo | [Public Data License 1.0 (CC BY 4.0 compatible)](https://maps.gsi.go.jp/development/ichiran.html) | 地理院タイル (GSI Japan) を加工して作成, PDL 1.0 |
| Canada, Ontario – Ontario Imagery Web Map Service | [Open Government Licence – Ontario](https://www.ontario.ca/page/open-government-licence-ontario) | Contains information licensed under the Open Government Licence – Ontario |
| Canada, Toronto – City of Toronto orthophoto | [Open Government Licence – Toronto](https://open.toronto.ca/open-data-license/) | Contains information licensed under the Open Government Licence – Toronto |
| United States – USDA NAIP | [Public domain](https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits) | Imagery: USDA NAIP (public domain), via Microsoft Planetary Computer and USGS The National Map |
| United States, Connecticut – CT ECO 2023 | [No use limitations (CT ECO metadata)](https://cteco.uconn.edu/download/metadata/CT_ortho_2023_metadata.htm) | Imagery: CT ECO, State of Connecticut, 2023 |
| United States, Indiana – IndianaMap current imagery | [CC0](https://registry.opendata.aws/in-imagery/) | Imagery: Indiana Geographic Information Office (CC0) |
| United States, Kentucky – KyFromAbove 3-inch | [Public domain with attribution](https://registry.opendata.aws/kyfromabove/) | Imagery: KyFromAbove, Kentucky Division of Geographic Information |
| United States, Massachusetts – MassGIS 2025 | [No restrictions (MassGIS)](https://www.arcgis.com/home/item.html?id=a3bbd504c80746ac9aedc7b5ca4e0d00) | Imagery: MassGIS 2025 |
| United States, Missouri – MSDIS 6-inch 2023–2024 | [CC BY 4.0](https://registry.opendata.aws/mo-gis-imagery/) | Imagery: Missouri Spatial Data Information Service (MSDIS), CC BY 4.0 |
| United States, North Carolina – NC OneMap orthoimagery | [Free and unrestricted use (NC OneMap)](https://www.nconemap.gov/pages/terms) | Imagery: NC OneMap, NC Center for Geographic Information and Analysis, NC 911 Board |
| United States, Vermont – VCGI best of colour | [Unrestricted use (Vermont Imagery Program)](https://vcgi.vermont.gov/node/156) | Imagery: VCGI, Vermont Imagery Program |
| United States, Washington DC – OCTO 2025 | [CC BY 4.0](https://imagery.dcgis.dc.gov/dcgis/rest/services/Ortho/Ortho_2025/ImageServer/info/metadata) | Imagery: Office of the Chief Technology Officer, District of Columbia, CC BY 4.0 |
| Brazil, Rio de Janeiro – IPP true orthophoto 2024 | [CC BY 4.0](https://pgeo3.rio.rj.gov.br/arcgis/rest/services/Imagens/Mosaico_2024/ImageServer/info/iteminfo) | Mosaico 2024, Instituto Pereira Passos, PCRJ, CC BY 4.0 |
| Australia, ACT – ACT Government aerial imagery | [CC BY 4.0](https://actmapi-actgov.opendata.arcgis.com/maps/ACTGOV::act-aerial-imagery-current/about) | Imagery CC BY 4.0 Australian Capital Territory and Aerometrex |
<!-- end sources.json table -->

NAIP is read from [Microsoft Planetary Computer](https://planetarycomputer.microsoft.com/dataset/naip)
(the original 0.6 m GeoTIFFs), with the USGS National Map service as map layer and fallback.
Planetary Computer's [terms of use](https://planetarycomputer.microsoft.com/terms) apply; no account
is needed. Spain's source leaves out Melilla, which is satellite imagery rather than PNOA, and Lower
Saxony's leaves out Bremen.

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
