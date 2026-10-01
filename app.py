#!/usr/bin/env python3
"""Универсальный генератор карточек: галерея шаблонов → страница шаблона (JSON + превью) → PNG.

Шаблоны — папки в packs/ (см. packs/README.md). Запуск: python app.py
"""
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path

import requests
from flask import Flask, abort, jsonify, render_template, request, send_from_directory

import packs as P
from generate import ASSETS_DIR, ManualDataError, to_data_uri

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "ui_output"
UPLOAD_DIR = BASE_DIR / "ui_uploads"
ALLOWED_IMG = {".png", ".jpg", ".jpeg", ".webp"}
MAX_UPLOAD_MB = 10
KEEP_SECONDS = 24 * 3600

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
# Не более N одновременных рендеров Chromium — иначе сервер упадёт по памяти
RENDER_SLOTS = threading.Semaphore(int(os.environ.get("RENDER_SLOTS", "2")))

OUTPUT_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)

app = Flask(__name__, template_folder="ui_templates")
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_MB * 1024 * 1024


def _pack_or_404(pack_id: str) -> dict:
    pack = P.load_packs().get(pack_id)
    if not pack:
        abort(404)
    return pack


def _example_text(pack: dict) -> str:
    return json.dumps(pack["example"], ensure_ascii=False, indent=2)


def _cleanup():
    """Удаляет результаты и загрузки старше суток (диск на хостинге не резиновый)."""
    now = time.time()
    for base in (OUTPUT_DIR, UPLOAD_DIR):
        for item in base.iterdir():
            try:
                if now - item.stat().st_mtime > KEEP_SECONDS:
                    shutil.rmtree(item) if item.is_dir() else item.unlink()
            except OSError:
                pass


# ------------------------------------------------------------------ страницы
@app.get("/")
def gallery():
    items = []
    for pack in P.load_packs().values():
        thumb, tw, th = None, pack["width"], pack["height"]
        try:  # миниатюра = первая карточка из примера шаблона
            r = P.build_renders(pack, pack["example"], P.PLACEHOLDER if pack["photo"] else None)[0]
            thumb, tw, th = P.render_html(pack, r, preview=True), r["w"], r["h"]
        except Exception:  # noqa: BLE001 — битый пример не должен ронять галерею
            pass
        items.append({**pack, "thumb": thumb, "tw": tw, "th": th})
    return render_template("gallery.html", packs=items)


@app.get("/t/<pack_id>")
def template_page(pack_id):
    pack = _pack_or_404(pack_id)
    return render_template("template.html", pack=pack, json_text=_example_text(pack), error=None)


@app.post("/t/<pack_id>/preview")
def preview(pack_id):
    pack = _pack_or_404(pack_id)
    b = request.get_json(silent=True) or {}
    raw = (b.get("json_data") or "").strip()
    if not raw:
        return jsonify(ok=True, cards=[])
    photo = b.get("photo") if str(b.get("photo", "")).startswith("data:image/") else P.PLACEHOLDER
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return jsonify(ok=False, error=f"Невалидный JSON: {e}")
    try:
        renders = P.build_renders(pack, data, photo)[:P.PREVIEW_MAX]
        cards = [{"label": r["label"], "w": r["w"], "h": r["h"], "html": P.render_html(pack, r, preview=True)}
                 for r in renders]
    except ManualDataError as e:
        return jsonify(ok=False, error=str(e))
    except Exception as e:  # noqa: BLE001 — неполный JSON не должен ронять превью
        return jsonify(ok=False, error=f"Не удалось построить превью: {e}")
    return jsonify(ok=True, cards=cards)


@app.post("/t/<pack_id>/generate")
def generate(pack_id):
    pack = _pack_or_404(pack_id)
    raw = (request.form.get("json_data") or "").strip()

    def fail(msg):
        return render_template("template.html", pack=pack, json_text=raw, error=msg), 400

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return fail(f"Невалидный JSON: {e}")

    _cleanup()
    job_id = uuid.uuid4().hex[:8]
    photo_uri = None
    upload = request.files.get("photo")
    if pack["photo"] and upload and upload.filename:
        ext = Path(upload.filename).suffix.lower()
        if ext not in ALLOWED_IMG:
            return fail("Фото: поддерживаются PNG, JPG, WEBP.")
        photo_path = UPLOAD_DIR / f"{job_id}{ext}"
        upload.save(photo_path)
        photo_uri = to_data_uri(photo_path)
    elif pack["photo"] == "required":
        return fail("Загрузите фото — оно обязательно для этого шаблона.")

    try:
        renders = P.build_renders(pack, data, photo_uri)
    except ManualDataError as e:
        return fail(str(e))
    except Exception as e:  # noqa: BLE001
        return fail(f"Ошибка в данных: {e}")

    out_dir = OUTPUT_DIR / job_id
    try:
        with RENDER_SLOTS:
            paths = P.render_pngs(pack, renders, out_dir)
    except Exception as e:  # noqa: BLE001
        return fail(f"Не удалось сгенерировать картинки: {e}")

    zip_path = out_dir / "enso-cards.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            z.write(p, p.name)
    cards = [{"file": f"{job_id}/{p.name}", "label": r["label"], "dims": f"{r['w']} × {r['h']}"}
             for p, r in zip(paths, renders)]
    return render_template("result.html", pack=pack, cards=cards, zip_file=f"{job_id}/{zip_path.name}")


# ------------------------------------------------------------------ файлы
@app.get("/output/<path:filename>")
def serve_output(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.get("/assets/<path:filename>")
def serve_asset(filename):
    return send_from_directory(ASSETS_DIR, filename)


@app.get("/packs/<pack_id>/assets/<path:filename>")
def serve_pack_asset(pack_id, filename):
    resp = send_from_directory(_pack_or_404(pack_id)["dir"] / "assets", filename)
    resp.headers["Access-Control-Allow-Origin"] = "*"  # шрифты внутри sandbox-iframe превью
    return resp


# ------------------------------------------------------------------ отправка в Telegram
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
        for i in range(0, len(pngs), 10):  # альбомы по 10 фото (сжатые Telegram)
            chunk = pngs[i:i + 10]
            media = [{"type": "photo", "media": f"attach://f{n}"} for n in range(len(chunk))]
            files = {f"f{n}": (p.name, p.read_bytes()) for n, p in enumerate(chunk)}
            if not _tg("sendMediaGroup", chat_id, files, {"media": json.dumps(media)}).ok:
                for p in chunk:  # например, файл >10 МБ — шлём документами
                    _tg("sendDocument", chat_id, {"document": (p.name, p.read_bytes())}, {})
        zp = job_dir / "enso-cards.zip"
        if zp.exists():  # оригиналы без сжатия
            _tg("sendDocument", chat_id, {"document": (zp.name, zp.read_bytes())},
                {"caption": "Оригиналы без сжатия"})
    except requests.RequestException as e:
        return jsonify(ok=False, error=str(e)), 502
    return jsonify(ok=True)


if __name__ == "__main__":
    # use_reloader=False обязательно: генерация пишет PNG/zip прямо в ui_output/
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
