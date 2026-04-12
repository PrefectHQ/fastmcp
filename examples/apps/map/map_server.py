"""Interactive Map — geocode addresses and render on an interactive map.

Accepts plain addresses (or place names), geocodes them via
OpenStreetMap Nominatim, and renders an interactive Leaflet map.

Usage:
    fastmcp dev apps map_server.py
"""

from __future__ import annotations

from textwrap import dedent

import httpx
from prefab_ui.app import PrefabApp
from prefab_ui.components import (
    Badge,
    Card,
    Column,
    Embed,
    Heading,
    Muted,
)
from prefab_ui.components.data_table import DataTable, DataTableColumn

from fastmcp import FastMCP

mcp = FastMCP("Interactive Map")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


def _geocode(query: str) -> dict | None:
    """Geocode an address using OpenStreetMap Nominatim (free, no key)."""
    resp = httpx.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": 1},
        headers={"User-Agent": "fastmcp-map-example/1.0"},
        timeout=10,
    )
    results = resp.json()
    if results:
        r = results[0]
        return {
            "name": r.get("display_name", query).split(",")[0],
            "address": query,
            "lat": float(r["lat"]),
            "lng": float(r["lon"]),
        }
    return None


def _build_map_html(
    locations: list[dict],
    zoom: int,
) -> str:
    markers_js = ""
    for loc in locations:
        name = str(loc["name"]).replace("\\", "\\\\").replace("'", "\\'")
        markers_js += (
            f"L.marker([{loc['lat']}, {loc['lng']}]).addTo(map).bindPopup('{name}');\n"
        )

    avg_lat = sum(loc["lat"] for loc in locations) / len(locations)
    avg_lng = sum(loc["lng"] for loc in locations) / len(locations)

    return dedent(f"""\
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="utf-8" />
            <link
                rel="stylesheet"
                href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
            />
            <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
            <style>
                body {{ margin: 0; }}
                #map {{ width: 100%; height: 100vh; }}
            </style>
        </head>
        <body>
            <div id="map"></div>
            <script>
                var map = L.map('map').setView([{avg_lat}, {avg_lng}], {zoom});
                L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
                    attribution: '&copy; OpenStreetMap contributors'
                }}).addTo(map);
                {markers_js}
            </script>
        </body>
        </html>
    """)


@mcp.tool(app=True)
def show_map(
    locations: list[str] | None = None,
    title: str = "Map",
    zoom: int = 2,
) -> PrefabApp:
    """Show locations on an interactive map.

    Accepts addresses, place names, or landmarks. Each location is
    geocoded via OpenStreetMap and displayed as a marker on an
    interactive Leaflet map.

    Args:
        locations: List of addresses or place names. Defaults to
            sample US landmarks if not provided.
        title: Heading for the map.
        zoom: Initial zoom level (1-18, higher = closer).
    """
    if not locations:
        locations = [
            "Statue of Liberty, New York",
            "Golden Gate Bridge, San Francisco",
            "Space Needle, Seattle",
            "Willis Tower, Chicago",
            "Gateway Arch, St. Louis",
        ]

    geocoded = []
    failed = []
    for loc in locations:
        result = _geocode(loc)
        if result:
            geocoded.append(result)
        else:
            failed.append(loc)

    with PrefabApp() as app:
        with Column(gap=4, css_class="p-6"):
            Heading(title)
            Muted(f"{len(geocoded)} locations mapped")
            if failed:
                for f in failed:
                    Badge(f"Could not find: {f}", variant="destructive")

            if geocoded:
                map_html = _build_map_html(geocoded, zoom)
                with Card():
                    Embed(
                        html=map_html,
                        width="100%",
                        height="500px",
                        sandbox="allow-scripts",
                    )
                DataTable(
                    columns=[
                        DataTableColumn(key="name", header="Name", sortable=True),
                        DataTableColumn(key="address", header="Address", sortable=True),
                        DataTableColumn(key="lat", header="Latitude", sortable=True),
                        DataTableColumn(key="lng", header="Longitude", sortable=True),
                    ],
                    rows=geocoded,
                    search=True,
                )

    return app


if __name__ == "__main__":
    mcp.run()
