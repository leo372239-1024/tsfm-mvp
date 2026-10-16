"""Fetch public source data and record source digests. Run from repository root."""
from pathlib import Path
from hashlib import sha256
from io import BytesIO
import gzip
import json
import zipfile
import requests
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
DATA.mkdir(exist_ok=True)
NAB = DATA / "nab" / "realTraffic"
NAB.mkdir(parents=True, exist_ok=True)

UCI_URL = "https://archive.ics.uci.edu/static/public/492/metro+interstate+traffic+volume.zip"
NAB_BASE = "https://raw.githubusercontent.com/numenta/NAB/ea702d75cc2258d9d7dd35ca8e5e2539d71f3140/"
NAB_FILES = ["TravelTime_387.csv", "TravelTime_451.csv", "occupancy_6005.csv",
             "occupancy_t4013.csv", "speed_6005.csv", "speed_7578.csv", "speed_t4013.csv"]


def download(url, path):
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)
    return {"url": url, "path": str(path.relative_to(ROOT)),
            "bytes": len(response.content), "sha256": sha256(response.content).hexdigest()}


def main():
    manifest = {"sources": [], "transforms": []}
    uci_zip = DATA / "source_metro.zip"
    manifest["sources"].append(download(UCI_URL, uci_zip))
    with zipfile.ZipFile(uci_zip) as archive:
        member = next(name for name in archive.namelist() if name.endswith(".csv.gz"))
        raw = gzip.decompress(archive.read(member))
    raw_path = DATA / "Metro_Interstate_Traffic_Volume.csv"
    raw_path.write_bytes(raw)
    frame = pd.read_csv(BytesIO(raw))
    # UCI describes local CST; use a fixed -06:00 offset because the source omits offsets.
    # Repeated local timestamps are aggregated; missing hours are left missing.
    frame["ts"] = pd.to_datetime(frame["date_time"], errors="raise").dt.tz_localize("Etc/GMT+6").dt.tz_convert("UTC")
    hourly = frame.groupby("ts", as_index=False)["traffic_volume"].median().rename(columns={"traffic_volume": "value"})
    hourly["value"] = hourly["value"].astype(float)
    hourly.to_csv(DATA / "metro_hourly.csv", index=False, date_format="%Y-%m-%dT%H:%M:%S%z")
    manifest["transforms"].append({"input": str(raw_path.relative_to(ROOT)),
        "output": "data/metro_hourly.csv", "rows_raw": len(frame), "rows_unique_hour": len(hourly),
        "method": "median of duplicate timestamps; fixed UTC-06 offset; missing hours retained as gaps",
        "sha256": sha256((DATA / "metro_hourly.csv").read_bytes()).hexdigest()})
    for name in NAB_FILES:
        path = NAB / name
        manifest["sources"].append(download(NAB_BASE + "data/realTraffic/" + name, path))
    labels = DATA / "nab" / "combined_windows.json"
    manifest["sources"].append(download(NAB_BASE + "labels/combined_windows.json", labels))
    license_path = DATA / "nab" / "LICENSE.txt"
    manifest["sources"].append(download(NAB_BASE + "LICENSE.txt", license_path))
    (DATA / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"uci_rows": len(hourly), "nab_files": len(NAB_FILES),
                      "manifest": str(DATA / "manifest.json")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

