"""Парсит GLB и выводит все blendshape (morph target) имена в каждом mesh.

Standard glTF 2.0:
  meshes[i].primitives[j].targets    — массив targets (по одному на blendshape)
  meshes[i].extras.targetNames       — имена blendshapes (расширение)
  meshes[i].primitives[j].extras.targetNames — иногда здесь

GLB структура:
  [12-byte header] [JSON chunk header + JSON] [BIN chunk header + BIN]
"""
from __future__ import annotations
import json, struct, sys
from pathlib import Path

GLB_PATH = sys.argv[1] if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[1] / "channels" / "web_hud" / "static" / "avatars" / "female_default.glb"
)
GLB_PATH = Path(GLB_PATH)

print(f"=== GLB blendshape inspector ===")
print(f"file: {GLB_PATH}  size: {GLB_PATH.stat().st_size:,}")

raw = GLB_PATH.read_bytes()
magic, ver, total = struct.unpack_from("<III", raw, 0)
assert magic == 0x46546C67, f"not a glb: magic={magic:x}"
print(f"version: {ver}  declared total: {total:,}")

# JSON chunk
chunk_len, chunk_type = struct.unpack_from("<II", raw, 12)
assert chunk_type == 0x4E4F534A, f"first chunk should be JSON: {chunk_type:x}"
json_bytes = raw[20:20 + chunk_len]
doc = json.loads(json_bytes)

meshes = doc.get("meshes", [])
print(f"\nmeshes: {len(meshes)}")

for mi, mesh in enumerate(meshes):
    name = mesh.get("name") or f"<mesh#{mi}>"
    primitives = mesh.get("primitives", [])
    target_names = (
        mesh.get("extras", {}).get("targetNames")
        or (primitives[0].get("extras", {}).get("targetNames") if primitives else None)
        or []
    )
    n_targets = len(primitives[0].get("targets", [])) if primitives else 0
    if n_targets == 0 and not target_names:
        continue
    print(f"\n  [{mi}] mesh: {name!r}")
    print(f"      primitives: {len(primitives)}  targets per primitive: {n_targets}")
    print(f"      targetNames ({len(target_names)}):")
    for ti, tn in enumerate(target_names):
        print(f"        {ti:3d}: {tn}")
