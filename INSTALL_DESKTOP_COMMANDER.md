# Установка Desktop Commander MCP — даём Джарвису руки на ПК

## Зачем
Чтобы Claude (Джарвис в Cowork) мог сам запускать PowerShell, ставить пакеты,
проверять состояние ПК — без ручного copy-paste команд от Босса.

Это **прототип execution-плагина** будущего Джарвиса. Позже либо обернём в плагин Ирины, либо перепишем под наш контракт.

---

## Шаги установки

### 0. Требования
- Node.js 18+ → https://nodejs.org/ (LTS)
- Проверка: `node --version` в PowerShell

### 1. Установка (автоустановщик)
```powershell
npx @wonderwhy-er/desktop-commander@latest setup
```

### 2. Если автоустановщик не нашёл конфиг — ручной режим
Открой:
```
C:\Users\Staho\AppData\Roaming\Claude\claude_desktop_config.json
```

Если файла нет — создай. Содержимое:
```json
{
  "mcpServers": {
    "desktop-commander": {
      "command": "npx",
      "args": ["-y", "@wonderwhy-er/desktop-commander@latest"]
    }
  }
}
```

Если в файле уже есть `"mcpServers": { ... }` — добавь ключ `"desktop-commander"` внутрь, не забыв запятую.

### 3. Перезапуск Claude Desktop
- Правый клик по иконке в трее → **Quit / Выход**
  (не просто крестик — он останется висеть в трее)
- Запусти Claude Desktop заново
- Открой этот же чат с Джарвисом

### 4. Проверка
Спроси у Джарвиса: «Видишь меня?» — он попробует вызвать
`execute_command("Get-Host")` и покажет результат.

---

## Безопасность — первый шаг после рестарта

Джарвис настроит блок-лист опасных команд через `set_config_value`:
- `format`
- `del /s`, `del /q`
- `rd /s`, `rmdir /s`
- `shutdown`, `restart-computer`
- `reg delete HKLM`, `reg delete HKCR`
- `rm -rf /`, `rm -rf C:`
- `bcdedit`
- `Remove-Item -Recurse -Force` на корни дисков

---

## Что появится у Джарвиса (17 tool'ов)

| Категория | Tool'ы |
|-----------|--------|
| Shell | `execute_command`, `read_output`, `force_terminate`, `list_sessions` |
| Файлы | `read_file`, `write_file`, `edit_block`, `move_file`, `list_directory` |
| Поиск | `search_files`, `search_code` |
| Процессы | `list_processes`, `kill_process` |
| Метаданные | `get_file_info`, `create_directory` |
| Конфиг | `get_config`, `set_config_value` |

---

## Если что-то не работает

- Логи Claude Desktop: `%APPDATA%\Claude\logs\`
- Логи Desktop Commander: запускаются вместе с Claude, видны в его логах
- Проверь, что в `claude_desktop_config.json` валидный JSON (можно вставить в jsonlint.com)
- Проверь, что Claude Desktop полностью закрыт через трей перед перезапуском

---

*Возвращайся в чат с Джарвисом после рестарта — продолжим.*
