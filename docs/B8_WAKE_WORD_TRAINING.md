# B8: Тренировка custom wake-word «Джарвис»

End-to-end процесс — от записи голоса Босса до подключения готовой модели в JARVIS.

## Архитектура

```
record_wake_samples.py        extract_wake_dataset.py          Colab notebook
  ─ raw/positive/   ─►  processed/positive/   ─►  dataset.zip
  ─ raw/negative/        processed/negative/         │
                                                     ▼
                                          [training 6-8 hours T4 GPU]
                                                     │
                                                     ▼
                                                dzarvis.onnx
                                                     │
                                                     ▼
                                          jarvis/models/wake/
                                                     │
                                                     ▼
                                            WakeDetector подхватывает
```

## Этапы

### 1. Запись samples (~12 минут твоего времени)

```powershell
cd "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis"
.\.venv\Scripts\python.exe scripts\record_wake_samples.py
```

Скрипт проведёт через **15 манер произнесения × 10 повторов = 150 positive samples**:
- тихо / громко
- радостно / устало / раздражённо
- как вопрос / как приказ
- шёпотом / быстро / растягивая
- издалека / близко к мику
- в середине / начале / конце фразы

Плюс **4 блока негативной речи по 60 сек** — обычный разговор без слова «Джарвис».

Если прервался — `--resume` продолжит с того же места.

**На выходе:** `workspace/wake_samples/raw/positive/*.wav` + `negative/*.wav`

### 2. Обработка dataset (~10 секунд)

```powershell
.\.venv\Scripts\python.exe scripts\extract_wake_dataset.py
```

Что делает:
- Trim silence по краям positives
- Нормализация амплитуды
- Нарезка negative блоков на 3-сек чанки с VAD-фильтром
- Упаковка в `workspace/wake_samples/dataset.zip`

**На выходе:** `workspace/wake_samples/dataset.zip` (~5-10 МБ)

### 3. Colab training (6-8 часов compute, твоё время — 15 мин setup)

#### 3.1. Открыть Colab

1. https://colab.research.google.com → New notebook
2. Runtime → Change runtime type → **T4 GPU** → Save
3. Файл → **Загрузи `scripts/train_wake_dzarvis.ipynb`** (или скопируй ячейки из README ниже)

#### 3.2. Альтернатива — скопируй ячейки из этого README в свой Colab notebook

**Ячейка 1 — Проверка GPU:**

```python
import torch
print('CUDA available:', torch.cuda.is_available())
print('Device:', torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'CPU')
```

Должно показать `Tesla T4` или похоже.

**Ячейка 2 — Установка openWakeWord + зависимостей:**

```python
!pip install -q openwakeword onnxruntime-gpu soundfile librosa audiomentations
!pip install -q torch torchaudio --index-url https://download.pytorch.org/whl/cu121
print('OK')
```

**Ячейка 3 — Загрузка dataset.zip:**

```python
from google.colab import files
print('Загрузи свой dataset.zip из workspace/wake_samples/dataset.zip:')
uploaded = files.upload()  # выбери dataset.zip
```

Или через Google Drive (если не хочешь загружать через браузер каждый раз):

```python
from google.colab import drive
drive.mount('/content/drive')
# Сначала положи dataset.zip в Drive вручную в /MyDrive/jarvis-wake/dataset.zip
!cp /content/drive/MyDrive/jarvis-wake/dataset.zip /content/
```

**Ячейка 4 — Распаковка:**

```python
import zipfile, os
with zipfile.ZipFile('dataset.zip') as zf:
    zf.extractall('/content/wake_data')
pos = os.listdir('/content/wake_data/positive')
neg = os.listdir('/content/wake_data/negative')
print(f'positive: {len(pos)} файлов')
print(f'negative: {len(neg)} файлов')
```

Должно показать ~150 positive и 50-100 negative.

**Ячейка 5 — Клонирование openwakeword training pipeline:**

```python
!git clone https://github.com/dscripka/openWakeWord.git /content/oww_repo
%cd /content/oww_repo
!pip install -e .
```

**Ячейка 6 — Конфигурация тренировки:**

```yaml
%%writefile /content/training_config.yaml
# Конфигурация для тренировки wake-word "джарвис"
model_name: dzarvis
target_phrase:
  - джарвис
  - джарвиз  # частая ошибка распознавания
  - джарвас

# Сгенерируем синтетические positive через TTS (русский голос) + ваши real samples
n_samples: 1000  # синтетические TTS-сэмплы
n_samples_val: 200
language: ru

# Реальные positive samples — Бóсс
positive_data:
  - /content/wake_data/positive

# Negative — обычная речь Босса + добавим публичный датасет
negative_data:
  - /content/wake_data/negative

# Augmentation
rir_paths:  # room impulse responses для разнообразия комнат
  - /content/openwakeword/data/rir
background_paths:  # фоновые звуки
  - /content/openwakeword/data/noise

# Training
batch_size: 128
learning_rate: 0.0001
n_epochs: 100
target_false_activations_per_hour: 0.5
output_dir: /content/dzarvis_model
```

**Ячейка 7 — Загрузка augmentation данных (RIRs + noise):**

```python
# openWakeWord training требует RIR и noise датасеты — небольшие подмножества из MIT/AudioSet
!cd /content/oww_repo && python -c "from openwakeword.utils import download_models; download_models()"
# Скачаем training augmentation data (~1-2 GB)
!mkdir -p /content/openwakeword/data/rir /content/openwakeword/data/noise
# Используем встроенные tutorial scripts:
%cd /content/oww_repo
!python notebooks/automatic_model_training.py --help 2>&1 | head -30
```

**Ячейка 8 — Запуск тренировки:**

```python
# Это займёт 6-8 часов на T4.
# Notebook автора (рекомендуется): открой и скопируй ячейки из
#   https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb
# Или используй CLI:

!python /content/oww_repo/openwakeword/train.py \
    --config /content/training_config.yaml \
    --output /content/dzarvis_model \
    --num_epochs 100 \
    --batch_size 128 \
    --learning_rate 1e-4 \
    --target_phrase "джарвис"
```

> **Важно**: `openwakeword.train` API может меняться от версии к версии.
> Если эта команда не работает — открой [официальный notebook](https://github.com/dscripka/openWakeWord/blob/main/notebooks/automatic_model_training.ipynb)
> и используй его ячейки целиком. Только в ячейке `target_word = "hey jarvis"` замени на `target_word = "джарвис"`,
> а в `custom_positive_samples` укажи путь `/content/wake_data/positive`.

**Ячейка 9 — Экспорт ONNX + скачивание:**

```python
import shutil
# Найти финальную модель
import glob
onnx_files = glob.glob('/content/dzarvis_model/**/*.onnx', recursive=True)
print('Найденные ONNX:', onnx_files)

# Выбираем финальную (обычно с самым большим step или final)
final = max(onnx_files, key=os.path.getmtime) if onnx_files else None
if final:
    target = '/content/dzarvis.onnx'
    shutil.copy(final, target)
    print(f'Готово: {target}')
    # Скачиваем
    from google.colab import files
    files.download(target)
```

### 4. Интеграция модели в JARVIS

После того как `dzarvis.onnx` скачался:

```powershell
# Положи модель в репо
$model = Read-Host "Полный путь к скачанному dzarvis.onnx"
mkdir -Force "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\models\wake"
Copy-Item $model "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis\models\wake\dzarvis.onnx"
```

В `jarvis/core/voice/wake.py` уже поддерживается custom модель через параметр `model_path` (см. B8.4).

Тестировать:
```powershell
# Запуск voice loop в wake-режиме
cd "C:\Users\Staho\Documents\Claude\Projects\ДЖАРВИС (2)\jarvis"
.\.venv\Scripts\python.exe run_voice.py wake
```

Если threshold нужно подкрутить — параметр в `WakeDetector(threshold=0.5)`. Понизишь — больше false positives, повысишь — пропускает тихие/нечёткие.

## Troubleshooting

**Модель не распознаёт «Джарвис»**
- Снизь threshold до 0.4 или 0.35
- Проверь что мик не в HFP-режиме (Bluetooth headset)
- Перетренируй с большим количеством positives в проблемном контексте

**Слишком много false positives**
- Подними threshold до 0.6-0.7
- Перетренируй с большим разнообразием negative samples

**Тренировка падает в Colab**
- Проверь что T4 GPU активен (не CPU)
- Уменьши batch_size до 64 если OOM
- Бесплатный Colab вырубается через 12 часов — сохраняй checkpoints в Drive

**openWakeWord training API сломался**
- Альтернатива: используй [Picovoice Console](https://console.picovoice.ai/) для кастомных wake-words (онлайн UI, бесплатно для personal, поддерживает русский)
- Качество сопоставимое, тренировка занимает минуты вместо часов

## Альтернатива — Picovoice Porcupine

Если openWakeWord training не получится:

1. Регистрируйся на https://console.picovoice.ai
2. Wake Words → Train Wake Word → Russian → введи «Джарвис»
3. Запиши/загрузи ~30 samples через их UI
4. Скачай `.ppn` файл
5. В коде: используй `pvporcupine` вместо `openwakeword` (другой SDK, но похожая интеграция)

Picovoice — коммерческий, но для одной wake-word и одного юзера бесплатно. Качество выше чем openWakeWord обычно.
