"""Create a checked source/data/evidence ZIP; never include local credentials."""
from hashlib import sha256
from pathlib import Path
import json
import re
import zipfile

ROOT = Path(__file__).resolve().parents[1]
manifest_path = ROOT / "evidence/manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
names = sorted(manifest["file_sha256"]) + ["evidence/manifest.json"]
for name in names:
    if name == ".env" or name.startswith("dist/") or "__pycache__" in name:
        raise RuntimeError("forbidden archive path")
    path = ROOT / name
    if not path.is_file():
        raise RuntimeError(f"missing file: {name}")
    if name in manifest["file_sha256"] and sha256(path.read_bytes()).hexdigest() != manifest["file_sha256"][name]:
        raise RuntimeError(f"changed since evidence freeze: {name}")
    if path.suffix.lower() in {".py", ".md", ".yaml", ".txt", ".json", ".example"}:
        data = path.read_bytes()
        if re.search(rb"sk-or-v1-[A-Za-z0-9]+|ts-ah[0-9A-Za-z_-]{30,}", data):
            raise RuntimeError(f"credential-like text in: {name}")

outdir = ROOT / "dist"
outdir.mkdir(exist_ok=True)
archive = outdir / "tsfm-mvp-20261016.zip"
if archive.exists():
    raise RuntimeError("release ZIP already exists; choose a new version instead of overwriting")
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=8) as z:
    for name in names:
        z.write(ROOT / name, arcname=name)
with zipfile.ZipFile(archive) as z:
    if z.testzip() is not None or sorted(z.namelist()) != sorted(names):
        raise RuntimeError("ZIP verification failed")
hash_value = sha256(archive.read_bytes()).hexdigest()
(outdir / "tsfm-mvp-20261016.sha256").write_text(f"{hash_value}  {archive.name}\n", encoding="ascii")
print(json.dumps({"archive": str(archive), "files": len(names), "sha256": hash_value}))

