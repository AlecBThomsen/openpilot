"""Keep the manifest readable by the updater running before a fork switch.

sunnypilot 289faac reads openpilot/system/hardware/comma/agnos.json from
the downloaded tree before it can activate that tree's own updater.
"""

import json
import re
import shutil
from pathlib import Path


BASEDIR = Path(__file__).resolve().parents[3]
CANONICAL_MANIFEST = Path("system/hardware/tici/agnos.json")
SUNNYPILOT_MANIFEST = Path("openpilot/system/hardware/comma/agnos.json")


def test_sunnypilot_manifest_resolves_to_current_manifest():
  legacy_path = BASEDIR / SUNNYPILOT_MANIFEST
  canonical_path = BASEDIR / CANONICAL_MANIFEST
  assert legacy_path.resolve(strict=True) == canonical_path
  assert json.loads(legacy_path.read_text()) == json.loads(canonical_path.read_text())


def test_sunnypilot_manifest_survives_staging_move(tmp_path):
  # Updaters copy symlinks into a finalized tree, then move that tree at boot.
  staged = tmp_path / "finalized"
  for relative_path in (
    Path("openpilot/system"),
    Path("system/hardware/comma/agnos.json"),
    CANONICAL_MANIFEST,
  ):
    target = staged / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BASEDIR / relative_path, target, follow_symlinks=False)
  installed = tmp_path / "openpilot"
  staged.rename(installed)
  assert (installed / SUNNYPILOT_MANIFEST).resolve(strict=True) == installed / CANONICAL_MANIFEST
  assert json.loads((installed / SUNNYPILOT_MANIFEST).read_text())


def test_manifest_matches_sunnypilot_flasher_schema():
  # These fields are consumed by the old flasher; newer optional fields are ignored.
  manifest = json.loads((BASEDIR / SUNNYPILOT_MANIFEST).read_text())
  assert isinstance(manifest, list) and manifest
  assert len({partition["name"] for partition in manifest}) == len(manifest)
  for partition in manifest:
    assert isinstance(partition["name"], str) and partition["name"]
    assert partition["url"].startswith("https://")
    assert type(partition["size"]) is int and partition["size"] > 0
    for field in ("hash", "hash_raw"):
      assert re.fullmatch(r"[0-9a-f]{64}", partition[field])
    for field in ("sparse", "full_check"):
      assert type(partition[field]) is bool
    assert type(partition.get("has_ab", True)) is bool
