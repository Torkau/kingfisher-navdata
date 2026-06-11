#!/usr/bin/env python3
"""Erzeugt sector_boundaries.js (window.EMBEDDED_BOUNDARIES) aus der oeffentlichen
IVAO-Datei "IVAO ATC Positions YYYYMMDD.zip" (enthaelt atc_positions.json).

Rein aus IVAO-Daten, ohne Navigraph-Anteile, damit der GitHub-Spiegel sich
lizenzsauber automatisch aktualisieren laesst (per GitHub Action).

Aufruf:
    python build_boundaries.py <eingabe.zip|atc_positions.json> [ziel_ordner]

Schreibt in den Zielordner (Standard: aktuelles Verzeichnis):
    sector_boundaries.js   - window.EMBEDDED_BOUNDARIES = {...};
    version.json           - {"boundaries":"YYYYMMDD"}

atc_positions.json ist ein Array aus Objekten:
    Center (CTR/FSS):  center_id, middle_identifier, position, name, type, map_region
    Airport (sonst):   airport_id, middle_identifier, position, name, type, map_region
map_region ist eine Liste aus {"lat":..,"lng":..}.

Mehrere Volumina einer Position (z.B. Hoehenbaender von EDWW_CTR) werden, sofern
shapely verfuegbar ist, zur Aussenkontur verschmolzen (Union). Ueberlappende
Flaechen ergeben so eine saubere Grenze, wirklich getrennte Gebiete bleiben als
mehrere Ringe erhalten. Ohne shapely werden die Rohpolygone uebernommen.
Koordinaten werden als [lon, lat] abgelegt (Schema der App).
"""
import sys
import os
import re
import json
import zipfile
import struct
import zlib
import datetime

# Build-Revision: bei Konverter-Aenderungen (z.B. Union) erhoehen, damit die App
# die neue Datei auch bei gleichem IVAO-Datum nachlaedt. Format: <YYYYMMDD>-<rev>.
BUILD_REV = "u1"

try:
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    _HAVE_SHAPELY = True
except Exception:
    _HAVE_SHAPELY = False


def _read_atc_json_from_zip(path):
    """Liest atc_positions.json aus dem Zip. Nutzt zuerst das normale zipfile
    Modul; faellt bei beschaedigtem/abgeschnittenem Archiv auf Roh-Deflate
    zurueck (liefert dann ggf. nur einen Teil; im Produktivlauf irrelevant)."""
    try:
        with zipfile.ZipFile(path) as z:
            name = next((n for n in z.namelist()
                         if n.lower().endswith("atc_positions.json")), None)
            if not name:
                raise RuntimeError("atc_positions.json nicht im Zip gefunden")
            return z.read(name).decode("utf-8", "ignore")
    except zipfile.BadZipFile:
        pass
    d = open(path, "rb").read()
    pos = 0
    while True:
        j = d.find(b"PK\x03\x04", pos)
        if j < 0:
            raise RuntimeError("Kein atc_positions.json im (defekten) Zip")
        n = struct.unpack_from("<H", d, j + 26)[0]
        m = struct.unpack_from("<H", d, j + 28)[0]
        nm = d[j + 30:j + 30 + n].decode("utf-8", "ignore")
        if nm.lower().endswith("atc_positions.json"):
            comp = d[j + 30 + n + m:]
            dobj = zlib.decompressobj(-15)
            out = b""
            try:
                for k in range(0, len(comp), 262144):
                    out += dobj.decompress(comp[k:k + 262144])
            except Exception:
                pass
            return out.decode("utf-8", "ignore")
        pos = j + 4


def _parse_array(txt):
    """Parst das JSON-Array. Bei abgeschnittenem Text werden nur die
    vollstaendigen Objekte uebernommen."""
    txt = txt.lstrip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    if not txt.startswith("["):
        raise RuntimeError("Unerwartetes JSON-Format")
    s = txt[1:]
    dec = json.JSONDecoder()
    arr = []
    idx = 0
    L = len(s)
    while idx < L:
        while idx < L and s[idx] in " ,\r\n\t":
            idx += 1
        if idx >= L or s[idx] != "{":
            break
        try:
            obj, end = dec.raw_decode(s, idx)
        except Exception:
            break
        arr.append(obj)
        idx = end
    return arr


def _coords(map_region):
    out = []
    for p in map_region:
        try:
            out.append([round(float(p["lng"]), 5), round(float(p["lat"]), 5)])
        except Exception:
            continue
    return out


def _union_rings(sectors):
    """Verschmilzt ueberlappende Polygone einer Position zur Aussenkontur.
    Gibt eine Liste von Ringen [[lon,lat],...] zurueck. Ohne shapely oder bei
    Fehlern werden die Eingabepolygone unveraendert zurueckgegeben."""
    if not _HAVE_SHAPELY or len(sectors) < 2:
        return sectors
    polys = []
    for ring in sectors:
        if len(ring) < 3:
            continue
        try:
            p = Polygon(ring)
            if not p.is_valid:
                p = p.buffer(0)
            if (not p.is_empty) and p.is_valid:
                polys.append(p)
        except Exception:
            continue
    if not polys:
        return sectors
    try:
        u = unary_union(polys)
    except Exception:
        return sectors
    geoms = list(u.geoms) if hasattr(u, "geoms") else [u]
    out = []
    for g in geoms:
        try:
            if g.is_empty:
                continue
            ext = list(g.exterior.coords)
            ring = [[round(float(x), 5), round(float(y), 5)] for (x, y) in ext]
            if len(ring) >= 3:
                out.append(ring)
        except Exception:
            continue
    return out or sectors


def build(arr, version):
    # CTR/FSS: pro (icao, middle) alle Teilpolygone sammeln, dann Union.
    # APP/DEP/...: pro (icao, middle, position) bei Dubletten groesstes Polygon.
    fir_map = {}
    ap_map = {}
    for o in arr:
        posn = (o.get("position") or "").upper()
        coords = _coords(o.get("map_region") or [])
        if len(coords) < 3:
            continue
        mid = o.get("middle_identifier") or ""
        if posn in ("CTR", "FSS"):
            icao = o.get("center_id") or o.get("airport_id") or ""
            if not icao:
                continue
            e = fir_map.get((icao, mid))
            if not e:
                e = {"icao": icao, "middle": mid,
                     "name": o.get("name") or icao, "sectors": []}
                fir_map[(icao, mid)] = e
            e["sectors"].append(coords)
        else:
            icao = o.get("airport_id") or o.get("center_id") or ""
            if not icao:
                continue
            k = (icao, mid, posn)
            e = ap_map.get(k)
            if (not e) or len(coords) > len(e["coords"]):
                ap_map[k] = {"icao": icao, "middle": mid, "position": posn,
                             "name": o.get("name") or icao, "coords": coords}
    for e in fir_map.values():
        e["sectors"] = _union_rings(e["sectors"])
    ver = version + ("-" + BUILD_REV if BUILD_REV else "")
    return {
        "type": "IVAO Sector Boundaries",
        "source": "IVAO ATC Positions " + version + (" (" + BUILD_REV + ")" if BUILD_REV else ""),
        "version": ver,
        "airport_sectors": list(ap_map.values()),
        "fir_sectors": list(fir_map.values()),
    }


def _version_from(path, arr):
    m = re.search(r"(\d{8})", os.path.basename(path))
    if m:
        return m.group(1)
    return datetime.date.today().strftime("%Y%m%d")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    inp = sys.argv[1]
    out_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    if inp.lower().endswith(".zip"):
        txt = _read_atc_json_from_zip(inp)
    else:
        txt = open(inp, "r", encoding="utf-8", errors="ignore").read()
    arr = _parse_array(txt)
    version = _version_from(inp, arr)
    data = build(arr, version)
    with open(os.path.join(out_dir, "sector_boundaries.js"), "w", encoding="utf-8") as f:
        f.write("window.EMBEDDED_BOUNDARIES = ")
        json.dump(data, f, separators=(",", ":"), ensure_ascii=False)
        f.write(";\n")
    with open(os.path.join(out_dir, "version.json"), "w", encoding="utf-8") as f:
        json.dump({"boundaries": data["version"]}, f)
    print("Version:", data["version"],
          "| shapely:", _HAVE_SHAPELY,
          "| airport_sectors:", len(data["airport_sectors"]),
          "| fir_sectors:", len(data["fir_sectors"]))


if __name__ == "__main__":
    main()
