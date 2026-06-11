#!/usr/bin/env python3
"""Erzeugt ourairports.sqlite aus den oeffentlichen (Public Domain) CSV-Dateien
von OurAirports. Dient als LNM-freie Quelle, um in der Slim-DB Flugplaetze,
Pisten und Navaids aufzufuellen, die Navigraph nicht hat (kleine Tour-/Addon-
Felder).

Aufruf (laedt CSVs selbst herunter):
    python build_ourairports.py [ziel_ordner]

Schreibt:
    ourairports.sqlite
    ourairports_version.json   - {"ourairports":"YYYYMMDD"}

Tabellen:
    oa_airport(ident PK, type, name, lat, lon, elev_ft, continent,
               iso_country, iso_region, municipality, gps_code, iata_code,
               local_code, keywords)
    oa_runway(airport_ident, length_ft, width_ft, surface, lighted, closed,
              le_ident, le_lat, le_lon, le_elev, le_heading, le_disp,
              he_ident, he_lat, he_lon, he_elev, he_heading, he_disp)
    oa_navaid(ident, name, type, freq_khz, lat, lon, elev_ft, iso_country,
              dme_freq_khz, associated_airport)

Frequenzen/Wikipedia bewusst NICHT in oa_airport (Frequenzen macht IVAO).
"""
import sys
import os
import csv
import io
import json
import sqlite3
import datetime
import urllib.request

BASE = "https://davidmegginson.github.io/ourairports-data/"
FILES = {"airports": "airports.csv", "runways": "runways.csv", "navaids": "navaids.csv"}


def _download(name):
    url = BASE + FILES[name]
    req = urllib.request.Request(url, headers={"User-Agent": "kingfisher-navdata-bot"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = resp.read().decode("utf-8", "ignore")
    return list(csv.DictReader(io.StringIO(data)))


def _f(v):
    try:
        return float(v)
    except Exception:
        return None


def _i(v):
    try:
        return int(float(v))
    except Exception:
        return None


def build(out_dir):
    con = sqlite3.connect(os.path.join(out_dir, "ourairports.sqlite"))
    cur = con.cursor()
    cur.executescript("""
        DROP TABLE IF EXISTS oa_airport;
        DROP TABLE IF EXISTS oa_runway;
        DROP TABLE IF EXISTS oa_navaid;
        CREATE TABLE oa_airport(
            ident TEXT PRIMARY KEY, type TEXT, name TEXT, lat REAL, lon REAL,
            elev_ft INTEGER, continent TEXT, iso_country TEXT, iso_region TEXT,
            municipality TEXT, gps_code TEXT, iata_code TEXT, local_code TEXT,
            keywords TEXT);
        CREATE TABLE oa_runway(
            airport_ident TEXT, length_ft INTEGER, width_ft INTEGER, surface TEXT,
            lighted INTEGER, closed INTEGER,
            le_ident TEXT, le_lat REAL, le_lon REAL, le_elev INTEGER,
            le_heading REAL, le_disp INTEGER,
            he_ident TEXT, he_lat REAL, he_lon REAL, he_elev INTEGER,
            he_heading REAL, he_disp INTEGER);
        CREATE TABLE oa_navaid(
            ident TEXT, name TEXT, type TEXT, freq_khz INTEGER, lat REAL, lon REAL,
            elev_ft INTEGER, iso_country TEXT, dme_freq_khz INTEGER,
            associated_airport TEXT);
    """)

    aps = _download("airports")
    n_ap = 0
    for r in aps:
        ident = (r.get("ident") or "").strip().upper()
        lat = _f(r.get("latitude_deg")); lon = _f(r.get("longitude_deg"))
        if not ident or lat is None or lon is None:
            continue
        cur.execute(
            "INSERT OR IGNORE INTO oa_airport VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ident, r.get("type") or "", r.get("name") or "", lat, lon,
             _i(r.get("elevation_ft")), r.get("continent") or "",
             r.get("iso_country") or "", r.get("iso_region") or "",
             r.get("municipality") or "", (r.get("gps_code") or "").strip().upper(),
             (r.get("iata_code") or "").strip().upper(),
             (r.get("local_code") or "").strip().upper(), r.get("keywords") or ""))
        n_ap += 1

    rws = _download("runways")
    n_rw = 0
    for r in rws:
        ai = (r.get("airport_ident") or "").strip().upper()
        if not ai:
            continue
        cur.execute(
            "INSERT INTO oa_runway VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ai, _i(r.get("length_ft")), _i(r.get("width_ft")), r.get("surface") or "",
             _i(r.get("lighted")), _i(r.get("closed")),
             (r.get("le_ident") or "").strip().upper(), _f(r.get("le_latitude_deg")),
             _f(r.get("le_longitude_deg")), _i(r.get("le_elevation_ft")),
             _f(r.get("le_heading_degT")), _i(r.get("le_displaced_threshold_ft")),
             (r.get("he_ident") or "").strip().upper(), _f(r.get("he_latitude_deg")),
             _f(r.get("he_longitude_deg")), _i(r.get("he_elevation_ft")),
             _f(r.get("he_heading_degT")), _i(r.get("he_displaced_threshold_ft"))))
        n_rw += 1

    nvs = _download("navaids")
    n_nv = 0
    for r in nvs:
        ident = (r.get("ident") or "").strip().upper()
        lat = _f(r.get("latitude_deg")); lon = _f(r.get("longitude_deg"))
        if not ident or lat is None or lon is None:
            continue
        cur.execute(
            "INSERT INTO oa_navaid VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ident, r.get("name") or "", r.get("type") or "", _i(r.get("frequency_khz")),
             lat, lon, _i(r.get("elevation_ft")), r.get("iso_country") or "",
             _i(r.get("dme_frequency_khz")), (r.get("associated_airport") or "").strip().upper()))
        n_nv += 1

    cur.executescript("""
        CREATE INDEX idx_oa_rw_ai ON oa_runway(airport_ident);
        CREATE INDEX idx_oa_nv_id ON oa_navaid(ident);
        CREATE INDEX idx_oa_ap_iata ON oa_airport(iata_code);
    """)
    con.commit()
    con.close()
    ver = datetime.date.today().strftime("%Y%m%d")
    with open(os.path.join(out_dir, "ourairports_version.json"), "w", encoding="utf-8") as f:
        json.dump({"ourairports": ver}, f)
    print("ourairports.sqlite gebaut | airports:", n_ap, "runways:", n_rw,
          "navaids:", n_nv, "| version:", ver)


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    build(out)
