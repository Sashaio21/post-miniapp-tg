# 円相 (Enso) — генератор карточек, Telegram Mini App

JSON → PNG-карточки, отрендеренные headless-Chromium (Playwright). Flask-интерфейс
открывается внутри Telegram, готовые карточки отправляются в чат с ботом.

Главная страница — галерея шаблонов. Клик по шаблону открывает его страницу: поле JSON
(с примером), живое превью и кнопка «Сгенерировать PNG». Шаблоны — папки в `packs/`;
новый шаблон добавляется без правки Python (см. `packs/README.md`).

## Структура

```
app.py          Flask: галерея, страница шаблона, превью, генерация PNG/zip, /send в Telegram
packs.py        загрузка паков из packs/, builders, рендер HTML и PNG
core.py         хелперы и сборка контекстов (github-карточка, пакет «провал стартапа»)
bot.py          бот на long polling: /start и кнопка меню, открывающая Mini App
packs/          шаблоны (template.json + .html.j2 + assets/)
ui_templates/   страницы интерфейса (Flask/Jinja)
assets/         логотип-кольцо
```

Паки: `gh-story`/`gh-post`/`gh-telegram` (GitHub-проект, нужно фото),
`startup-telegram`/`startup-post`/`startup-story` (обложка), `startup-carousel`
(7 слайдов), `f1-pulse`, `example-quote` (демо без кода). Пример JSON для каждого
лежит в его `template.json` и подставляется в поле ввода.

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

Переменные окружения — в `.env.example` (`BOT_TOKEN`, `WEBAPP_URL`, `PORT`, `RENDER_SLOTS` —
число одновременных рендеров Chromium).

## Docker / Render

`docker build -t enso-bot . && docker run -p 5000:5000 --env-file .env enso-bot`
(за reverse-proxy с HTTPS: Caddy / nginx). Для Render есть `render.yaml`; если рендер
падает по памяти, поставьте план `standard` (2 ГБ).

## Ограничения прототипа

- Нет проверки `initData` (подпись HMAC) — любой может дёргать `/send` и рендер.
- Текст в шаблонах рендерится без экранирования — не давайте доступ чужим.
- Результаты в `ui_output/` чистятся только при следующей генерации (старше 24 часов).
