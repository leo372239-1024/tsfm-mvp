"""Hash the delivered sources, public data, models, test evidence and running image."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "evidence"


def digest(path):
    return sha256(path.read_bytes()).hexdigest()


def docker_id(target, image=False):
    args = ["docker", "image", "inspect", target, "--format", "{{.Id}}"] if image else ["docker", "inspect", target, "--format", "{{.Image}}"]
    return subprocess.check_output(args, text=True).strip()


def main():
    source_manifest = json.loads((ROOT / "data/manifest.json").read_text(encoding="utf-8"))
    for item in source_manifest["sources"]:
        path = ROOT / item["path"]
        if not path.exists() or digest(path) != item["sha256"]:
            raise RuntimeError(f"source data hash mismatch: {item['path']}")
    for item in source_manifest["transforms"]:
        path = ROOT / item["output"]
        if not path.exists() or digest(path) != item["sha256"]:
            raise RuntimeError(f"transformed data hash mismatch: {item['output']}")
    files = []
    for name in ("README.md", "Dockerfile", "compose.yaml", "requirements.txt", ".env.example", ".dockerignore", ".gitignore"):
        files.append(ROOT / name)
    for dirname in ("app", "scripts", "docs", "data", "third_party", "artifacts"):
        files.extend(path for path in (ROOT / dirname).rglob("*") if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc")
    files.extend(path for path in EVIDENCE.glob("*") if path.is_file() and path.name != "manifest.json")
    file_hashes = {str(path.relative_to(ROOT)).replace("\\", "/"): digest(path) for path in sorted(set(files))}
    image_id = docker_id("tsfm_mvp-api:latest", image=True)
    running_id = docker_id("tsfm_mvp-api-1")
    if image_id != running_id:
        raise RuntimeError("running API image differs from built candidate")
    manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(),
                "api_image_id": image_id,
                "db_image_id": docker_id("tsfm_mvp-db-1"),
                "graph_image_id": docker_id("tsfm_mvp-graph-1"),
                "file_sha256": file_hashes,
                "excluded": [".env", "database volumes", "provider credentials"]}
    EVIDENCE.mkdir(exist_ok=True)
    (EVIDENCE / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"files": len(file_hashes), "api_image_id": image_id,
                      "manifest": "evidence/manifest.json"}))


if __name__ == "__main__":
    main()

