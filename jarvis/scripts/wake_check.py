"""Проверка openwakeword: скачать модели + загрузить hey_jarvis."""

import sys

try:
    import openwakeword.utils

    print("Downloading hey_jarvis_v0.1 in onnx...", flush=True)
    openwakeword.utils.download_models(model_names=["hey_jarvis_v0.1"])
    from openwakeword.model import Model

    m = Model(wakeword_models=["hey_jarvis"], inference_framework="onnx")
    print("OK models:", list(m.models.keys()), flush=True)
except Exception as e:
    import traceback

    traceback.print_exc()
    sys.exit(1)
