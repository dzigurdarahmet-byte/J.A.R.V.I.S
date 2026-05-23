"""Импортируем build_app, билдим FastAPI app и просим openapi() — получаем настоящую traceback."""
from __future__ import annotations
import sys, traceback, warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

print("=== diag_openapi ===", flush=True)
print(f"python: {sys.version.split()[0]}", flush=True)

import fastapi, starlette, pydantic
print(f"fastapi  {fastapi.__version__}")
print(f"starlette {starlette.__version__}")
print(f"pydantic  {pydantic.VERSION}")
print(f"pydantic.BaseModel.__module__ = {pydantic.BaseModel.__module__}")

print("\n--- import + build app ---", flush=True)
try:
    from channels.web_hud.server import build_app
    from core.config import settings
    from core.providers import build_smart_provider
    from core.memory import MemoryManager
    WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"
    claude = build_smart_provider(settings)
    memory = MemoryManager(workspace_dir=WORKSPACE_DIR)
    app = build_app(claude, memory)
    print(f"app built OK — routes: {len(app.routes)}", flush=True)
except Exception:
    print("[FAIL] build_app упал:")
    traceback.print_exc()
    sys.exit(2)

# Найдём avatar_speak route и посмотрим как FastAPI его парсит
print("\n--- route inspection: /api/avatar/speak ---", flush=True)
target = None
for route in app.routes:
    path = getattr(route, "path", "")
    if path == "/api/avatar/speak":
        target = route
        break

if target is None:
    print("[FAIL] route не зарегистрирован!")
    sys.exit(3)

print(f"  endpoint: {target.endpoint}")
print(f"  methods:  {getattr(target, 'methods', None)}")
deps = getattr(target, "dependant", None)
if deps is not None:
    print(f"  dependant.body_params:   {[p.name for p in deps.body_params]}")
    print(f"  dependant.query_params:  {[p.name for p in deps.query_params]}")
    print(f"  dependant.path_params:   {[p.name for p in deps.path_params]}")
    print(f"  dependant.header_params: {[p.name for p in deps.header_params]}")

print("\n--- app.openapi() ---", flush=True)
try:
    schema = app.openapi()
    print(f"openapi OK — paths: {list(schema.get('paths', {}).keys())[:5]}...")
    print(f"\n/api/avatar/speak endpoint schema:")
    import json
    print(json.dumps(schema["paths"]["/api/avatar/speak"], ensure_ascii=False, indent=2))
except Exception:
    print("[FAIL] app.openapi() упал — ЭТО НАШ БАГ:")
    traceback.print_exc()
    sys.exit(4)
