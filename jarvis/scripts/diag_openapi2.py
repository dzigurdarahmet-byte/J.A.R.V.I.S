"""Глубокая инспекция type hints — почему FastAPI считает req query?"""
from __future__ import annotations
import sys, traceback, typing, warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=DeprecationWarning)
HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# дублируем вывод в файл
LOG = open(HERE / "workspace" / "diag_openapi2.log", "w", encoding="utf-8")
def log(*args):
    line = " ".join(str(a) for a in args)
    print(line, flush=True)
    LOG.write(line + "\n")
    LOG.flush()

log("=== diag_openapi2 ===")

import fastapi, starlette, pydantic
from pydantic import BaseModel
log(f"fastapi {fastapi.__version__}  starlette {starlette.__version__}  pydantic {pydantic.VERSION}")
log(f"pydantic.BaseModel: {pydantic.BaseModel} module={pydantic.BaseModel.__module__}")

# 1) Импортируем server module и смотрим на AvatarSpeakRequest В НАТУРЕ
log("\n--- import server module ---")
from channels.web_hud import server as srv

# build_app определяет класс внутри — нет доступа извне. Так что
# повторяем регистрацию вручную, как FastAPI делает.

log("\n--- mimic build_app, just the avatar_speak part ---")
from fastapi import FastAPI
from pydantic import BaseModel as PBM
app = FastAPI()

class AvatarSpeakRequest(PBM):
    text: str
    emotion: str = "neutral"

log(f"AvatarSpeakRequest MRO: {[c.__name__ for c in AvatarSpeakRequest.__mro__]}")
log(f"AvatarSpeakRequest is BaseModel subclass: {issubclass(AvatarSpeakRequest, PBM)}")
log(f"pydantic.BaseModel id: {id(PBM)}")

@app.post("/api/avatar/speak")
async def avatar_speak(req: AvatarSpeakRequest) -> dict:
    return {"got_text": req.text}

# Что говорит FastAPI про этот route?
for route in app.routes:
    if getattr(route, "path", "") == "/api/avatar/speak":
        log(f"\nroute methods: {route.methods}")
        d = route.dependant
        log(f"  body_params:  {[p.name for p in d.body_params]}")
        log(f"  query_params: {[p.name for p in d.query_params]}")
        break

# type hints прямо у endpoint
log(f"\ntype hints of avatar_speak: {typing.get_type_hints(avatar_speak)}")

# 2) Теперь — попробуем openapi и поймаем traceback
log("\n--- app.openapi() ---")
try:
    schema = app.openapi()
    log(f"openapi OK")
    import json
    log(json.dumps(schema["paths"]["/api/avatar/speak"], ensure_ascii=False, indent=2))
except Exception:
    log("[FAIL] app.openapi() упал:")
    LOG.write(traceback.format_exc() + "\n")
    LOG.flush()
    traceback.print_exc()

# 3) Импорт реального build_app и тот же тест
log("\n=== now the REAL build_app ===")
try:
    from channels.web_hud.server import build_app
    from core.config import settings
    from core.providers import build_smart_provider
    from core.memory import MemoryManager
    WORKSPACE_DIR = Path(__file__).resolve().parents[1] / "workspace"
    claude = build_smart_provider(settings)
    memory = MemoryManager(workspace_dir=WORKSPACE_DIR)
    real_app = build_app(claude, memory)
    for route in real_app.routes:
        if getattr(route, "path", "") == "/api/avatar/speak":
            d = route.dependant
            log(f"REAL body_params:  {[p.name for p in d.body_params]}")
            log(f"REAL query_params: {[p.name for p in d.query_params]}")
            # копнём глубже — что в типе?
            for p in d.query_params:
                log(f"  query param {p.name}:")
                log(f"    type_:    {p.type_}")
                log(f"    field_info: {p.field_info}")
            break
    try:
        schema = real_app.openapi()
        log("REAL openapi: OK")
    except Exception:
        log("REAL openapi: [FAIL]")
        LOG.write(traceback.format_exc() + "\n")
        LOG.flush()
        traceback.print_exc()
except Exception:
    log("[FAIL] real build_app:")
    LOG.write(traceback.format_exc() + "\n")
    LOG.flush()
    traceback.print_exc()

LOG.close()
print("\n[done] лог в workspace/diag_openapi2.log")
