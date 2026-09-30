#!/usr/bin/env python3
"""
円相 (Enso) — веб-интерфейс к generate.py.

Только JSON — без обращения к GitHub API. Два типа карточек:

  github   — подборки GitHub-проектов, нужно фото/скриншот. Два режима:
             одиночная карточка (JSON-объект + фото) или подборка
             (JSON-список + фото по числу элементов).
  startup  — разбор ОДНОГО провалившегося стартапа. Только один режим:
             один JSON-объект → сразу весь пакет (telegram-обложка +
             7 слайдов карусели). Фото не нужно, режима «подборка» нет.

Запуск:
    python app.py

Открыть в браузере: http://127.0.0.1:5000
"""

import json
import os
import re
import threading
import uuid
import zipfile
from pathlib import Path

import requests
from urllib.parse import quote

from flask import Flask, jsonify, render_template, request, send_from_directory

from jinja2 import Environment, FileSystemLoader

from generate import (
    ASSETS_DIR,
    TEMPLATE_CATALOG,
    catalog_ids,
    default_ids,
    github_formats,
    github_templates,
    render_startup_items,
    startup_items,
    STARTUP_TELEGRAM_H,
    STARTUP_TELEGRAM_W,
    TEMPLATES_DIR,
    to_data_uri,
    MANUAL_JSON_EXAMPLE,
    MANUAL_JSON_EXAMPLE_STARTUP,
    RING_ASSET,
    ManualDataError,
    build_context_manual,
    build_startup_package,
    parse_batch_items,
    render_all,
    render_startup_package,
)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "ui_uploads"
OUTPUT_DIR = BASE_DIR / "ui_output"
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Не более 2 одновременных рендеров Chromium — иначе сервер упадёт по памяти
RENDER_SLOTS = threading.Semaphore(int(os.environ.get("RENDER_SLOTS", "2")))
UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

ALLOWED_PHOTO_EXT = {".png", ".jpg", ".jpeg", ".webp"}

BATCH_JSON_EXAMPLE = [
    MANUAL_JSON_EXAMPLE,
    {
        "project_name": "cal.com",
        "repo_path": "calcom/cal.com",
        "clone_url": "https://github.com/calcom/cal.com.git",
        "tagline": "Open-source альтернатива Calendly",
        "description": "Система планирования встреч с открытым кодом — можно развернуть на своём сервере.",
        "stars": 34000,
    },
]

app = Flask(__name__, template_folder="ui_templates")
app.config["MAX_CONTENT_LENGTH"] = 60 * 1024 * 1024  # 60 МБ — с запасом на подборку из нескольких фото

_JSON_EXAMPLES = {
    "github": json.dumps(MANUAL_JSON_EXAMPLE, ensure_ascii=False, indent=2),
    "startup": json.dumps(MANUAL_JSON_EXAMPLE_STARTUP, ensure_ascii=False, indent=2),
}
_BATCH_EXAMPLE_STR = json.dumps(BATCH_JSON_EXAMPLE, ensure_ascii=False, indent=2)


def _render_index(**kwargs):
    """render_template('index.html', ...) но с гарантированными примерами JSON —
    они нужны шаблону всегда (в том числе в JS), не только при первом GET."""
    kwargs.setdefault("json_examples", _JSON_EXAMPLES)
    kwargs.setdefault("batch_example", _BATCH_EXAMPLE_STR)
    kwargs.setdefault("templates_catalog", TEMPLATE_CATALOG)
    kwargs.setdefault("selected_templates", [])
    return render_template("index.html", **kwargs)


def _sel(ids, group):
    """Оставляет только id шаблонов нужной группы. None (ничего не пришло) → по умолчанию."""
    if not ids:
        return None
    valid = set(catalog_ids(group))
    return [i for i in ids if i in valid]


def _ext_ok(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_PHOTO_EXT


def _make_zip(files: list, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=f.name)


@app.get("/")
def index():
    return _render_index()


@app.post("/generate")
def generate_view():
    card_type = request.form.get("card_type", "github")
    if card_type not in ("github", "startup"):
        card_type = "github"
    mode = request.form.get("mode", "single") if card_type == "github" else "startup"

    json_raw = request.form.get("json_data", "").strip()
    sel = _sel(request.form.getlist("templates"), card_type)
    form_state = {"json_data": json_raw, "mode": mode, "card_type": card_type, "selected_templates": sel or []}

    if not json_raw:
        return _render_index(error="Вставьте JSON с данными карточки.", **form_state), 400
    if not RING_ASSET.exists():
        return _render_index(error="Не найден ассет кольца в assets/ — переустановите проект.", **form_state), 500

    if not sel:
        return _render_index(error="Отметьте хотя бы один шаблон в списке.", **form_state), 400

    job_id = uuid.uuid4().hex[:8]
    job_out_dir = OUTPUT_DIR / job_id

    # ================= STARTUP: один пакет (обложка + 7 слайдов) =================
    if card_type == "startup":
        try:
            data = json.loads(json_raw)
        except json.JSONDecodeError as e:
            return _render_index(error=f"Невалидный JSON: {e}", **form_state), 400
        if isinstance(data, list):
            return _render_index(
                error="Для «Провал стартапа» нужен ОДИН JSON-объект (не список) — один стартап, весь пакет из 8 картинок за раз.",
                **form_state,
            ), 400

        try:
            pkg = build_startup_package(data)
        except ManualDataError as e:
            return _render_index(error=str(e), **form_state), 400

        try:
            items = startup_items(pkg, sel)
            with RENDER_SLOTS:
                paths = render_startup_items(items, job_out_dir, pkg["slug"])
        except Exception as e:
            return _render_index(error=f"Ошибка рендера: {e}", **form_state), 500

        zip_path = job_out_dir / "enso-cards.zip"
        _make_zip(paths, zip_path)

        cards = [{"file": f"{job_id}/{p.name}", "label": it["label"], "dims": f"{it['w']} × {it['h']}"}
                 for p, it in zip(paths, items)]

        return render_template(
            "result_startup.html",
            project_name=pkg["slug"],
            cards=cards,
            zip_file=f"{job_id}/enso-cards.zip",
        )

    # ================= GITHUB: ПОДБОРКА =================
    if mode == "batch":
        photos = [f for f in request.files.getlist("photos") if f.filename]
        if not photos:
            return _render_index(error="Загрузите фото — по одному на каждый элемент подборки.", **form_state), 400
        for f in photos:
            if not _ext_ok(f.filename):
                return _render_index(error=f"Файл «{f.filename}» — не PNG/JPG/WEBP.", **form_state), 400

        try:
            items = parse_batch_items(json_raw)
        except ManualDataError as e:
            return _render_index(error=str(e), **form_state), 400

        if len(photos) != len(items):
            return _render_index(
                error=f"Фото ({len(photos)}) и элементов в JSON ({len(items)}) должно быть поровну — они сопоставляются по порядку.",
                **form_state,
            ), 400

        total = len(items)
        sets = []
        all_files = []
        for i, (item, photo) in enumerate(zip(items, photos), start=1):
            ext = Path(photo.filename).suffix.lower()
            photo_path = UPLOAD_DIR / f"{job_id}-{i}{ext}"
            photo.save(photo_path)
            try:
                context = build_context_manual(item, photo_path, index=i, total=total, is_batch=True)
            except ManualDataError as e:
                return _render_index(error=f"Элемент №{i}: {e}", **form_state), 400

            slug = f"{i:02d}-{context['project_name'].lower().replace(' ', '-')}"
            try:
                with RENDER_SLOTS:
                    render_all(context, job_out_dir, slug, formats=github_formats(sel))
            except Exception as e:
                return _render_index(error=f"Ошибка рендера (элемент №{i}): {e}", **form_state), 500

            cards = [{"file": f"{job_id}/{slug}-{t['suffix']}.png", "label": t["name"], "dims": t["dims"]}
                     for t in github_templates(sel)]
            all_files += [job_out_dir / Path(c["file"]).name for c in cards]
            sets.append({"label": context["project_name"], "index": i, "total": total, "cards": cards})

        zip_path = job_out_dir / "enso-cards.zip"
        _make_zip(all_files, zip_path)

        return render_template(
            "result.html",
            is_batch=True,
            sets=sets,
            zip_file=f"{job_id}/enso-cards.zip",
        )

    # ================= GITHUB: ОДИНОЧНАЯ КАРТОЧКА =================
    photo = request.files.get("photo")
    if not photo or photo.filename == "":
        return _render_index(error="Загрузите фото/скриншот проекта.", **form_state), 400
    if not _ext_ok(photo.filename):
        return _render_index(error="Формат не поддерживается — нужен PNG, JPG или WEBP.", **form_state), 400

    try:
        data = json.loads(json_raw)
    except json.JSONDecodeError as e:
        return _render_index(error=f"Невалидный JSON: {e}", **form_state), 400
    if isinstance(data, list):
        return _render_index(
            error="Это похоже на список — для нескольких карточек переключитесь на вкладку «Подборка».",
            **form_state,
        ), 400

    ext = Path(photo.filename).suffix.lower()
    photo_path = UPLOAD_DIR / f"{job_id}{ext}"
    photo.save(photo_path)

    try:
        context = build_context_manual(data, photo_path, index=1, total=1, is_batch=False)
    except ManualDataError as e:
        return _render_index(error=str(e), **form_state), 400

    slug = f"{context['project_name'].lower().replace(' ', '-')}-{job_id}"
    try:
        with RENDER_SLOTS:
            render_all(context, job_out_dir, slug, formats=github_formats(sel))
    except Exception as e:
        return _render_index(error=f"Ошибка рендера: {e}", **form_state), 500

    cards = [{"file": f"{job_id}/{slug}-{t['suffix']}.png", "label": t["name"], "dims": t["dims"]}
             for t in github_templates(sel)]
    zip_path = job_out_dir / "enso-cards.zip"
    _make_zip([job_out_dir / Path(c["file"]).name for c in cards], zip_path)

    return render_template(
        "result.html",
        is_batch=False,
        sets=[{"label": context["project_name"], "index": 1, "total": 1, "cards": cards}],
        zip_file=f"{job_id}/enso-cards.zip",
    )


def _tg(method: str, chat_id: int, files: dict, data: dict):
    return requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
                         data={"chat_id": chat_id, **data}, files=files, timeout=120)


@app.post("/send")
def send_to_chat():
    """Отправляет готовые PNG в чат пользователя (без авторизации — прототип)."""
    body = request.get_json(silent=True) or {}
    job_id, chat_id = str(body.get("job_id", "")), body.get("chat_id")
    if not BOT_TOKEN:
        return jsonify(ok=False, error="BOT_TOKEN не задан на сервере"), 500
    if not re.fullmatch(r"[0-9a-f]{8}", job_id) or not isinstance(chat_id, int):
        return jsonify(ok=False, error="неверные параметры"), 400
    job_dir = OUTPUT_DIR / job_id
    pngs = sorted(job_dir.glob("*.png"))
    if not pngs:
        return jsonify(ok=False, error="файлы не найдены"), 404
    try:
        # 1) превью: альбомы по 10 фото (сжатые Telegram)
        for i in range(0, len(pngs), 10):
            chunk = pngs[i:i + 10]
            media = [{"type": "photo", "media": f"attach://f{n}"} for n in range(len(chunk))]
            files = {f"f{n}": (p.name, p.read_bytes()) for n, p in enumerate(chunk)}
            r = _tg("sendMediaGroup", chat_id, files, {"media": json.dumps(media)})
            if not r.ok:  # например, файл >10 МБ — шлём документами
                for p in chunk:
                    _tg("sendDocument", chat_id, {"document": (p.name, p.read_bytes())}, {})
        # 2) оригиналы без сжатия — одним zip
        zp = job_dir / "enso-cards.zip"
        if zp.exists():
            _tg("sendDocument", chat_id, {"document": (zp.name, zp.read_bytes())},
                {"caption": "Оригиналы без сжатия"})
    except requests.RequestException as e:
        return jsonify(ok=False, error=str(e)), 502
    return jsonify(ok=True)


# ======================= ПРЕДПРОСМОТР (без Chromium) =======================
# Шаблоны — это обычный HTML, поэтому превью рисуется прямо в браузере из
# Jinja-рендера. Картинки PNG (Playwright) делаются только в /generate.
_env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
_RING_URI = to_data_uri(RING_ASSET) if RING_ASSET.exists() else None
_PLACEHOLDER = "data:image/svg+xml;utf8," + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'>"
    "<rect width='800' height='600' fill='#23221a'/>"
    "<text x='400' y='310' fill='#8a8676' font-size='40' text-anchor='middle' "
    "font-family='sans-serif'>Фото проекта</text></svg>")
PREVIEW_MAX_ITEMS = 5


def _lite(html: str) -> str:
    """Кольцо (~260 КБ base64) заменяем короткой ссылкой — браузер кэширует."""
    return html.replace(_RING_URI, "/assets/" + RING_ASSET.name) if _RING_URI else html


@app.get("/assets/<path:filename>")
def serve_asset(filename):
    return send_from_directory(ASSETS_DIR, filename)


@app.post("/preview")
def preview():
    b = request.get_json(silent=True) or {}
    card_type = "startup" if b.get("card_type") == "startup" else "github"
    mode = b.get("mode", "single")
    raw = (b.get("json_data") or "").strip()
    sel = b.get("templates")
    sel = _sel(sel, card_type) if sel else ([] if sel == [] else None)
    photo = b.get("photo") if str(b.get("photo", "")).startswith("data:image/") else _PLACEHOLDER
    if not raw:
        return jsonify(ok=True, cards=[])
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify(ok=False, error=f"Невалидный JSON: {e}")

    cards = []  # (подпись, шаблон, контекст, ширина, высота)
    try:
        if card_type == "startup":
            if isinstance(data, list):
                raise ManualDataError("Для стартапа нужен один JSON-объект, не список.")
            pkg = build_startup_package(data)
            for it in startup_items(pkg, sel):
                cards.append((it["label"], it["template"], it["ctx"], it["w"], it["h"]))
        else:
            if mode == "batch":
                items = parse_batch_items(raw)[:PREVIEW_MAX_ITEMS]
            elif isinstance(data, list):
                raise ManualDataError("Это список — переключитесь на «Подборка».")
            else:
                items = [data]
            for n, item in enumerate(items, start=1):
                ctx = build_context_manual(item, RING_ASSET, n, len(items), mode == "batch")
                ctx["photo_data_uri"] = photo
                for t in github_templates(sel):
                    cards.append((f"{ctx['project_name']} · {t['name']}", t["file"], ctx, t["w"], t["h"]))
        out = [{"label": lb, "w": w, "h": h, "html": _lite(_env.get_template(tpl).render(**ctx))}
               for lb, tpl, ctx, w, h in cards]
    except ManualDataError as e:
        return jsonify(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001 — неполный JSON не должен ронять превью
        return jsonify(ok=False, error=f"Не удалось построить превью: {e}")
    return jsonify(ok=True, cards=out)


@app.get("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


if __name__ == "__main__":
    # use_reloader=False обязательно: реген пишет PNG/zip прямо в ui_output/
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=False, threaded=True)
