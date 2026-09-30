#!/usr/bin/env python3
"""Минимальный бот: /start → кнопка, открывающая Mini App. Long polling, без доп. зависимостей."""
import os
import time

import requests

TOKEN = os.environ["BOT_TOKEN"]
WEBAPP_URL = os.environ.get("WEBAPP_URL") or os.environ["RENDER_EXTERNAL_URL"]  # на Render задаётся автоматически
API = f"https://api.telegram.org/bot{TOKEN}"


def call(method, **params):
    return requests.post(f"{API}/{method}", json=params, timeout=40).json()


def main():
    # Кнопка-меню рядом с полем ввода
    call("setChatMenuButton", menu_button={"type": "web_app", "text": "Карточки", "web_app": {"url": WEBAPP_URL}})
    offset = 0
    print("Бот запущен")
    while True:
        try:
            upd = call("getUpdates", offset=offset, timeout=30).get("result", [])
        except requests.RequestException:
            time.sleep(3)
            continue
        for u in upd:
            offset = u["update_id"] + 1
            msg = u.get("message") or {}
            if msg.get("text", "").startswith("/start"):
                call("sendMessage", chat_id=msg["chat"]["id"],
                     text="円相 — генератор карточек.\nНажмите кнопку, чтобы открыть редактор.",
                     reply_markup={"inline_keyboard": [[{"text": "Открыть генератор", "web_app": {"url": WEBAPP_URL}}]]})


if __name__ == "__main__":
    main()
