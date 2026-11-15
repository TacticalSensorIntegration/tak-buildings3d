"""Keep the built-in imagery table in LICENSES.md in step with sources.json.

    python -m tak_buildings3d.licences      # rewrite the table
"""
from pathlib import Path

from .imagery import BUILTIN

LICENSES_MD = Path(__file__).parent.parent / "LICENSES.md"
START, END = "<!-- sources.json table -->", "<!-- end sources.json table -->"
CONTINENTS = ["Worldwide", "Europe", "Asia", "North America", "Central America", "South America",
              "Africa", "Oceania"]


def table():
    """The Markdown table of built-in sources, by continent then country."""
    rows = ["| Source | Licence | Attribution |", "|---|---|---|"]
    order = lambda s: (CONTINENTS.index(s["continent"]), s["country"], s["name"])
    for s in sorted(BUILTIN.values(), key=order):
        rows.append(f"| {s['name']} | [{s['licence']}]({s['licence_url']}) | {s['attribution']} |")
    return "\n".join(rows)


def updated(text):
    """text with the part between the markers replaced by the current table."""
    head, rest = text.split(START)
    return f"{head}{START}\n{table()}\n{END}{rest.split(END)[1]}"


if __name__ == "__main__":
    LICENSES_MD.write_text(updated(LICENSES_MD.read_text(encoding="utf-8")), encoding="utf-8")
