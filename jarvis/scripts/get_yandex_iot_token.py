"""Получить OAuth-токен Yandex IoT для управления умным домом.

Шаги:
  1) Зарегистрировать приложение в Yandex OAuth:
     https://oauth.yandex.ru/client/new

     Заполнить:
       - Название:          "JARVIS Personal Assistant"
       - Платформы:         Веб-сервисы
       - Redirect URI:      https://oauth.yandex.ru/verification_code
       - Доступ к данным:   найти "Yandex IoT" и поставить галки на
                            "iot:control" и "iot:view"
       - Email для связи:   твой email

  2) После создания скопировать ClientID из карточки приложения.

  3) Запустить этот скрипт:  python jarvis/scripts/get_yandex_iot_token.py
     Он откроет в браузере страницу авторизации.

  4) После авторизации Yandex покажет access_token (длинная строка)
     в URL после `#access_token=`. Скопировать ВСЁ значение до `&`.

  5) Положить в jarvis/.env (или корневой .env):
        YANDEX_IOT_TOKEN=<вставленный токен>

  6) Перезапустить JARVIS — skill подхватит токен и начнёт работать.

Токен живёт долго (до года) — обновлять не нужно. Если разлогинишься
в Yandex — токен слетит, повтори процедуру.
"""
from __future__ import annotations

import sys
import urllib.parse
import webbrowser

DEFAULT_REDIRECT_URI = "https://oauth.yandex.ru/verification_code"


def main() -> None:
    print("=" * 60)
    print(" Yandex IoT OAuth Token — получение")
    print("=" * 60)
    print()
    print("Шаг 1: открой https://oauth.yandex.ru/client/new")
    print("       Создай приложение со scope 'Yandex IoT'.")
    print("       Скопируй ClientID.")
    print()
    client_id = input("Введи ClientID приложения (32 hex symbols): ").strip()
    if not client_id or len(client_id) < 16:
        print("ClientID невалидный. Прерываю.")
        sys.exit(1)

    auth_url = (
        "https://oauth.yandex.ru/authorize"
        + "?response_type=token"
        + f"&client_id={urllib.parse.quote(client_id)}"
    )

    print()
    print("Шаг 2: открываю в браузере страницу авторизации.")
    print(f"       URL: {auth_url}")
    print()
    print("       Если не открылся сам — скопируй URL и открой вручную.")
    print()

    try:
        webbrowser.open(auth_url, new=2)
    except Exception:
        pass

    print("Шаг 3: разреши приложению доступ к Yandex IoT.")
    print("       После согласия откроется страница 'Код подтверждения'.")
    print("       Скопируй access_token из URL (между #access_token= и &)")
    print()
    token = input("Вставь access_token сюда: ").strip()
    if not token or len(token) < 10:
        print("Токен невалидный.")
        sys.exit(1)

    print()
    print("Шаг 4: положи в .env (или дополни существующий):")
    print()
    print(f"  YANDEX_IOT_TOKEN={token}")
    print()
    print("Шаг 5: перезапусти JARVIS — skill подхватит токен.")
    print()
    print("Проверка: скажи Джарвису 'список устройств'.")
    print("=" * 60)


if __name__ == "__main__":
    main()
