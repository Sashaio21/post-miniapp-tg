# Telegram Mini App — прототип

## Как устроено
Главная страница — галерея шаблонов. Клик по шаблону открывает его страницу: поле JSON (с примером),
живое превью и кнопка «Сгенерировать PNG». Шаблоны — папки в `packs/`; новый шаблон добавляется
без правки Python (см. `packs/README.md`). Для примера есть `packs/example-quote`.

Обёртка над существующим проектом: Flask-интерфейс открывается внутри Telegram,
результат отправляется кнопкой «Отправить карточки в чат» (Bot API).
Авторизации нет — чат определяется по `initDataUnsafe` (только для прототипа!).

## Запуск локально
1. Создайте бота в @BotFather, получите токен.
2. `pip install -r requirements.txt && playwright install chromium`
3. Поднимите HTTPS-туннель: `cloudflared tunnel --url http://localhost:5000` (или `ngrok http 5000`).
4. ```bash
   export BOT_TOKEN=... WEBAPP_URL=https://<адрес-туннеля>
   python app.py &      # Mini App на :5000
   python bot.py        # бот: /start → кнопка
   ```
5. Напишите боту `/start` и нажмите «Открыть генератор».

## Docker
`docker build -t enso-bot . && docker run -p 5000:5000 --env-file .env enso-bot`
(за reverse-proxy с HTTPS: Caddy / nginx).

## Ограничения прототипа
- Нет проверки `initData` (подпись HMAC) — любой может дёргать `/send` и рендер.
- Данные в `ui_output/` не чистятся автоматически.
- Текст в шаблонах рендерится без экранирования — не давайте доступ чужим.
