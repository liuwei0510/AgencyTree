"""Build the install folder and ZIP using the working source tree."""
from pathlib import Path
from shutil import copy2, copytree, ignore_patterns, rmtree
import hashlib
import json
from zipfile import ZipFile, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
version = json.loads((ROOT / "package.json").read_text())["version"]
dist = ROOT / "dist"
install = dist / f"agencytree-dsh-{version}"
if install.exists():
    rmtree(install)
install.mkdir(parents=True, exist_ok=True)
for name in ("package.json", "cordis.patch.yml", "README.md", "agent-registration.json"):
    copy2(ROOT / name, install)
ignore = ignore_patterns("__pycache__", ".DS_Store", "*.pyc", ".env", ".venv")
for name in ("src", "python", "vendor"):
    copytree(ROOT / name, install / name, dirs_exist_ok=True, ignore=ignore)
sdk = ROOT / "vendor" / "software-factory-runtime-sdk-0.4.2.tgz"
provenance = (ROOT / "vendor" / "SHA256SUMS.txt").read_text()
assert hashlib.sha256(sdk.read_bytes()).hexdigest() in provenance
zip_path = dist / f"{install.name}.zip"
with ZipFile(zip_path, "w", compression=ZIP_DEFLATED, compresslevel=6) as target:
    for path in sorted(install.rglob("*")):
        if path.is_file():
            target.write(path, path.relative_to(dist))
with ZipFile(zip_path) as archive:
    assert archive.testzip() is None
print(zip_path)
