"""Получить OAuth-токен Yandex Music для воспроизведения через JARVIS.

Yandex Music использует свой публичный client_id мобильного приложения.
Этот скрипт открывает страницу авторизации, после согласия Yandex отдаёт
access_token в URL — копируем в .env.

Шаги:
  1) Запустить скрипт: python jarvis/scripts/get_yandex_music_token.py
  2) В браузере залогиниться в Yandex-аккаунт, под которым у тебя есть
     Yandex Music Plus (или хотя бы лайки).
  3) Yandex покажет URL с access_token=<token> — скопировать токен.
  4) Положить в .env:
        YANDEX_MUSIC_TOKEN=<токен>
  5) Перезапустить JARVIS.

ВАЖНО: используется публичный client_id мобильного приложения Yandex
Music. Это известное community-решение для самоинтеграций. Yandex его
не запрещает (тот же id используют yandex-music-api и Home Assistant).
"""
from __future__ import annotations

import sys
import webbrowser

# Публичный client_id Yandex Music app (community-known).
# https://github.com/MarshalX/yandex-music-api/wiki/Получение-токена
YANDEX_MUSIC_CLIENT_ID = "23cabbbdc6cd418abb4b39c32c41195d"


def main() -> None:
    print("=" * 60)
    print(" Yandex Music OAuth Token — получение")
    print("=" * 60)
    print()

    auth_url = (
        "https://oauth.yandex.ru/authorize"
        "?response_type=token"
        f"&client_id={YANDEX_MUSIC_CLIENT_ID}"
    )

    print("Шаг 1: открываю в браузере страницу авторизации Yandex Music.")
    print(f"  URL: {auth_url}")
    print()
    print("Шаг 2: залогинься в нужный Yandex-аккаунт (с подпиской на Music).")
    print("Шаг 3: разреши приложению доступ.")
    print("Шаг 4: скопируй access_token из URL после #access_token=")
    print("        (до символа &)")
    print()

    try:
        webbrowser.open(auth_url, new=2)
    except Exception:
        pass

    token = input("Вставь access_token сюда: ").strip()
    if not token or len(token) < 10:
        print("Токен невалидный.")
        sys.exit(1)

    print()
    print("Шаг 5: положи в .env (строкой):")
    print()
    print(f"  YANDEX_MUSIC_TOKEN={token}")
    print()
    print("Шаг 6: перезапусти JARVIS — MusicSkill подхватит токен.")
    print()
    print("Проверка: скажи Джарвису 'включи музыку' или 'поставь <название>'.")
    print("=" * 60)


if __name__ == "__main__":
    main()
