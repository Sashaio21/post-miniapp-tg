#!/usr/bin/env python3
"""
円相 (Enso) — генератор карточек.

Два типа карточек (--type):
  github   — подборки GitHub-проектов (фото/скриншот обязательно; можно
             одиночную карточку или подборку из нескольких проектов)
  startup  — разбор ОДНОГО провалившегося стартапа: один JSON → сразу
             весь пакет — обложка для Telegram (1 картинка) + карусель
             из 7 слайдов для TikTok/Reels/Stories (1080×1350 каждая).
             Фото не нужно — вместо него цифра-хук в кольце.
             Здесь нет режима «подборка»: один стартап = один пакет.

GitHub — одиночная карточка (футер — просто линия):
    python generate.py --json data.json --photo screenshot.png
    python generate.py owner/repo --photo screenshot.png     (данные с GitHub)

GitHub — подборка (список объектов, номер/всего проставляются автоматически):
    python generate.py --batch list.json --photos p1.png p2.png ...

Startup — пакет карточек одного стартапа:
    python generate.py --type startup --json startup.json

    python generate.py --json-example                    # пример JSON, type=github
    python generate.py --type startup --json-example     # пример JSON, type=startup

Опции:
    --photo    фото для одиночной карточки (только type=github)
    --photos   фото по порядку элементов — для подборки (только type=github)
    --out      папка для результатов, по умолчанию ./output

GitHub: три PNG на карточку (story 1080×1920, post 1080×1350, telegram
1080×1080). Startup: telegram 1080×1080 + 7×1080×1350 (карусель).
"""

import argparse
import base64
import json
import mimetypes
import re
import sys
from pathlib import Path

import requests
from jinja2 import Environment, FileSystemLoader
from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
ASSETS_DIR = BASE_DIR / "assets"
RING_ASSET = ASSETS_DIR / "enso-ring-transparent.png"

# (внутреннее имя, файл шаблона, ширина, высота) — для type=github
FORMATS_GITHUB = [
    ("story",    "story_1080x1920.html.j2",    1080, 1920),
    ("post",     "post_1080x1350.html.j2",     1080, 1350),
    ("telegram", "telegram_1080x1080.html.j2", 1080, 1080),
]

# Карусель провала стартапа — фиксированная структура, 7 слайдов + отдельная
# обложка для Telegram. Все карусельные слайды — 1080×1350.
STARTUP_SLIDE_W, STARTUP_SLIDE_H = 1080, 1350
STARTUP_TELEGRAM_W, STARTUP_TELEGRAM_H = 1080, 1080
N_CONTENT_SLIDES = 5          # слайды 2..6 — «кто/контекст/цифра/твист/что дальше»
N_TOTAL_SLIDES = N_CONTENT_SLIDES + 2  # + обложка (1) + rebuild (7) = 7


class RepoFetchError(Exception):
    """Не удалось получить данные репозитория с GitHub."""


class ManualDataError(Exception):
    """Некорректный JSON с готовыми данными карточки."""


def normalize_repo_input(raw: str) -> str:
    """
    Принимает любой из форматов:
      owner/repo
      https://github.com/owner/repo
      https://github.com/owner/repo.git
      https://github.com/owner/repo/
      git@github.com:owner/repo.git
    Возвращает "owner/repo".
    """
    s = raw.strip()
    s = re.sub(r"^git@github\.com:", "", s)
    s = re.sub(r"^https?://(www\.)?github\.com/", "", s)
    s = s.strip("/")
    s = re.sub(r"\.git$", "", s)
    return s


def fetch_repo(owner_repo: str) -> dict:
    """Тянет метаданные репозитория из GitHub REST API."""
    owner_repo = normalize_repo_input(owner_repo)
    url = f"https://api.github.com/repos/{owner_repo}"
    resp = requests.get(url, timeout=15, headers={"Accept": "application/vnd.github+json"})
    if resp.status_code == 404:
        raise RepoFetchError(f"Репозиторий «{owner_repo}» не найден на GitHub.")
    if resp.status_code == 403:
        raise RepoFetchError(
            "GitHub API вернул 403 — вероятно, исчерпан лимит анонимных запросов "
            "(60/час). Попробуйте позже или добавьте токен в fetch_repo()."
        )
    resp.raise_for_status()
    return resp.json()


def format_stars(n: int) -> str:
    """22500 -> '22.5k', 850 -> '850'."""
    if n >= 1000:
        value = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{value}k"
    return str(n)


def to_data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def build_tagline(description: str, limit: int = 90) -> str:
    """Короткая версия описания под тег-лайн, если полное описание длинное."""
    description = description.strip()
    if len(description) <= limit:
        return description
    truncated = description[: limit - 1].rsplit(" ", 1)[0]
    return truncated + "…"


def build_context(repo_data: dict, photo_path: Path, index: int, total: int, is_batch: bool = True) -> dict:
    description = (repo_data.get("description") or "").strip()
    return {
        "project_name": repo_data["name"],
        "tagline": build_tagline(description) if description else "Описание не указано в репозитории — допишите вручную.",
        "desc_paragraph": description or "Добавьте описание проекта вручную в сгенерированном HTML/PNG.",
        "stars": format_stars(repo_data.get("stargazers_count", 0)),
        "repo_path": repo_data["full_name"],
        "clone_url": repo_data["clone_url"],
        "photo_data_uri": to_data_uri(photo_path),
        "ring_data_uri": to_data_uri(RING_ASSET),
        "index": f"{index:02d}",
        "total": f"{total:02d}",
        "is_batch": is_batch,
    }


# Поля, которые понимает build_context_manual — держите этот список и
# JSON-пример в UI/README синхронными.
MANUAL_JSON_EXAMPLE = {
    "project_name": "ai-job-search",
    "repo_path": "MadsLorentzen/ai-job-search",
    "clone_url": "https://github.com/MadsLorentzen/ai-job-search.git",
    "tagline": "Локальный ИИ-помощник для поиска работы — без данных на чужих серверах",
    "description": "Анализирует вакансии и подстраивает резюме под каждую из них — прямо на вашей машине, бесплатно и в открытом коде.",
    "stars": 22500,
}


def build_context_manual(data: dict, photo_path: Path, index: int, total: int, is_batch: bool) -> dict:
    """
    Собирает контекст из готового JSON вместо запроса к GitHub API.
    Обязательное поле — project_name, остальные необязательны.

    Пример JSON см. в MANUAL_JSON_EXAMPLE выше.
    "stars" может быть числом (22500 -> "22.5k") или уже готовой строкой ("22.5k").
    "index"/"total" в JSON, если заданы, перекрывают переданные index/total.

    is_batch управляет футером карточки: True — «円相 · подборка · NN/NN»,
    False — просто тонкая линия без текста (одиночная карточка).
    """
    if not isinstance(data, dict):
        raise ManualDataError("Каждый элемент должен быть JSON-объектом (словарём), например см. пример в форме.")

    project_name = (data.get("project_name") or "").strip()
    if not project_name:
        raise ManualDataError("В JSON не хватает обязательного поля «project_name».")

    description = (data.get("description") or "").strip()
    tagline = (data.get("tagline") or "").strip()
    if not tagline:
        tagline = build_tagline(description) if description else "Добавьте tagline или description в JSON."

    stars_raw = data.get("stars", "")
    if isinstance(stars_raw, (int, float)):
        stars = format_stars(int(stars_raw))
    else:
        stars = str(stars_raw).strip() or "—"

    repo_path = str(data.get("repo_path") or "").strip()
    clone_url = str(data.get("clone_url") or "").strip()
    if not clone_url and repo_path:
        clone_url = f"https://github.com/{repo_path}.git"

    idx = data.get("index", index)
    tot = data.get("total", total)
    try:
        idx = int(idx)
        tot = int(tot)
    except (TypeError, ValueError):
        raise ManualDataError("«index» и «total» в JSON должны быть числами.")

    return {
        "project_name": project_name,
        "tagline": tagline,
        "desc_paragraph": description or "Добавьте описание вручную.",
        "stars": stars,
        "repo_path": repo_path or "—",
        "clone_url": clone_url or "—",
        "photo_data_uri": to_data_uri(photo_path),
        "ring_data_uri": to_data_uri(RING_ASSET),
        "index": f"{idx:02d}",
        "total": f"{tot:02d}",
        "is_batch": is_batch,
    }


def parse_batch_items(raw_json: str) -> list:
    """Разбирает JSON-текст подборки, ожидая список объектов. (Только type=github.)"""
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        raise ManualDataError(f"Невалидный JSON: {e}")
    if not isinstance(data, list) or not data:
        raise ManualDataError("Для подборки JSON должен быть непустым списком объектов: [ {...}, {...} ].")
    for i, item in enumerate(data, start=1):
        if not isinstance(item, dict):
            raise ManualDataError(f"Элемент №{i} в списке — не объект (словарь).")
    return data


def render_all(context: dict, out_dir: Path, slug: str, formats=None) -> None:
    """Рендерит один и тот же context в несколько форматов (только type=github)."""
    formats = formats or FORMATS_GITHUB
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, tpl_file, width, height in formats:
            html = env.get_template(tpl_file).render(**context)
            page = browser.new_page(
                viewport={"width": width, "height": height},
                device_scale_factor=2,  # ретина-качество для соцсетей
            )
            page.set_content(html, wait_until="networkidle")
            out_path = out_dir / f"{slug}-{name}.png"
            page.locator(".card").screenshot(path=str(out_path))
            page.close()
            print(f"  ✓ {out_path}")
        browser.close()


# ======================================================================
# type = startup — карусель разбора одного провалившегося стартапа
# ======================================================================

MANUAL_JSON_EXAMPLE_STARTUP = {
    "project_name": "Frank",
    "flag": "🇺🇸",
    "years": "2016 — 2023",
    "sector": "Financials",

    "stat_num": "$175M",
    "stat_label": "сожжено",
    "tagline": "Сервис для быстрого заполнения FAFSA — заявки на студенческую финпомощь в США",

    "hook_headline": "JPMorgan купил стартап за <span class=\"accent\">$175M</span>. Оказалось — липа",
    "hook_subhead": "Разбор реальной истории →",

    "slides": [
        {
            "tag": "Кто это был",
            "headline": "Frank<br><span class=\"accent\">США, 2016</span>",
            "body": [
                "Бесплатный сервис для заполнения FAFSA — формы на получение студенческой финансовой помощи в США.",
                "Обещали пройти пугающую бюрократию за <b>10 минут</b> вместо нескольких часов."
            ],
            "meta": "СЕКТОР <b>Financials</b> · ОСНОВАН <b>2016</b> · ЗАКРЫТ <b>2023</b>"
        },
        {
            "tag": "Момент казался идеальным",
            "headline": "Всё было<br>за них",
            "body": [
                "Растущий студенческий долг в США, спрос на «потребительский» UX даже в госуслугах.",
                "Основательница Charlie Javice стала звездой — Forbes 30 under 30."
            ]
        },
        {
            "tag": "Цифра, которая всё решила",
            "headline": "Frank заявлял:<br><span class=\"accent\">4,25 млн</span> пользователей",
            "body": [
                "Именно эта цифра привлекла JPMorgan, который заплатил <b>$175M</b>, чтобы захватить аудиторию поколения Z."
            ]
        },
        {
            "tag": "Твист",
            "headline": "Реальных пользователей —<br><span class=\"accent\">~300 тысяч</span>",
            "body": [
                "После покупки JPMorgan решил проверить базу.",
                "Не 4,25 миллиона. <b>В 14 раз меньше.</b>"
            ]
        },
        {
            "tag": "Что было дальше",
            "headline": "Минюст США<br>предъявил обвинения",
            "body": [
                "2023 год — Charlie Javice обвиняют в мошенничестве.",
                "Идея продукта была рабочей. Провал — не в продукте, а в <b>цифрах, которые предъявили инвестору</b>."
            ]
        }
    ],

    "rebuild": {
        "idea": "Пошаговый помощник по целевому обучению и грантам — чеклист под вуз/регион, сроки подачи, шаблоны документов + база грантов и стипендий с фильтром по специальности и региону.",
        "stack": "Next.js + Supabase + Stripe/ЮKassa",
        "monetization": "Разовая оплата за проверку пакета документов или подписка для репетиторских центров, ведущих абитуриентов пачками."
    }
}


def _req_str(data: dict, key: str, where: str = "") -> str:
    val = str(data.get(key) or "").strip()
    if not val:
        raise ManualDataError(f"В JSON не хватает обязательного поля «{key}»{where}.")
    return val


def build_startup_package(data: dict) -> dict:
    """
    Проверяет и раскладывает JSON одного стартапа на контексты для всех
    8 карточек пакета: telegram-обложка + 7 слайдов карусели
    (1 hook + 5 content + 1 rebuild).

    Обязательные поля верхнего уровня: project_name, stat_num,
    hook_headline, slides (список ровно из 5 объектов), rebuild (объект
    с idea/stack обязательными; monetization необязательно — если не
    указано, поле «Монетизация» просто не рендерится). Остальное —
    необязательно, см. MANUAL_JSON_EXAMPLE_STARTUP.

    Возвращает {"slug": ..., "telegram": ctx, "slides": [ctx, ctx, ...]}
    — slides всегда длины 7 (cover + 5 content + rebuild), уже с
    проставленными index/total и ring_data_uri.
    """
    if not isinstance(data, dict):
        raise ManualDataError("Данные стартапа должны быть JSON-объектом (словарём) — см. пример в форме.")

    project_name = _req_str(data, "project_name")
    stat_num = _req_str(data, "stat_num")
    hook_headline = _req_str(data, "hook_headline")

    stat_label = (data.get("stat_label") or "сожжено").strip()
    hook_subhead = (data.get("hook_subhead") or "Разбор реальной истории →").strip()
    flag = (data.get("flag") or "").strip()
    years = (data.get("years") or "—").strip()
    sector = (data.get("sector") or "—").strip()

    description = (data.get("description") or "").strip()
    tagline = (data.get("tagline") or "").strip()
    if not tagline:
        tagline = build_tagline(description) if description else "Добавьте tagline или description в JSON."

    raw_slides = data.get("slides")
    if not isinstance(raw_slides, list) or len(raw_slides) != N_CONTENT_SLIDES:
        raise ManualDataError(
            f"«slides» должен быть списком ровно из {N_CONTENT_SLIDES} объектов "
            f"(кто это был / контекст / цифра / твист / что было дальше) — "
            f"сейчас {'не список' if not isinstance(raw_slides, list) else len(raw_slides)}."
        )

    rebuild = data.get("rebuild")
    if not isinstance(rebuild, dict):
        raise ManualDataError("В JSON не хватает объекта «rebuild» с полями idea/stack/monetization.")
    rebuild_idea = _req_str(rebuild, "idea", " (внутри rebuild)")
    rebuild_stack = _req_str(rebuild, "stack", " (внутри rebuild)")
    rebuild_monetization = (rebuild.get("monetization") or "").strip()
    rebuild_headline = (rebuild.get("headline") or "Версия<br>для СНГ").strip()

    ring_data_uri = to_data_uri(RING_ASSET)
    total = N_TOTAL_SLIDES

    telegram_ctx = {
        "project_name": project_name,
        "tagline": tagline,
        "stat_num": stat_num,
        "stat_label": stat_label,
        "flag": flag,
        "years": years,
        "sector": sector,
        "ring_data_uri": ring_data_uri,
    }

    slides = []

    # slide 1/total — cover
    slides.append({
        "_template": "startup_carousel_cover.html.j2",
        "_w": STARTUP_SLIDE_W, "_h": STARTUP_SLIDE_H,
        "_out_suffix": "slide01-cover",
        "stat_num": stat_num,
        "stat_label": stat_label,
        "hook_headline": hook_headline,
        "hook_subhead": hook_subhead,
        "ring_data_uri": ring_data_uri,
        "index": f"{1:02d}", "total": f"{total:02d}",
    })

    # slides 2..(1+N_CONTENT_SLIDES) — content
    for i, item in enumerate(raw_slides, start=2):
        if not isinstance(item, dict):
            raise ManualDataError(f"Элемент №{i - 1} в «slides» — не объект (словарь).")
        tag = _req_str(item, "tag", f" (slides[{i - 2}])")
        headline = _req_str(item, "headline", f" (slides[{i - 2}])")
        body = item.get("body") or []
        if not isinstance(body, list) or not all(isinstance(p, str) for p in body):
            raise ManualDataError(f"«body» в slides[{i - 2}] должен быть списком строк-абзацев.")
        meta = str(item.get("meta") or "").strip()
        slides.append({
            "_template": "startup_carousel_content.html.j2",
            "_w": STARTUP_SLIDE_W, "_h": STARTUP_SLIDE_H,
            "_out_suffix": f"slide{i:02d}",
            "tag": tag,
            "headline": headline,
            "body": body,
            "meta": meta,
            "ring_data_uri": ring_data_uri,
            "index": f"{i:02d}", "total": f"{total:02d}",
        })

    # last slide — rebuild
    slides.append({
        "_template": "startup_carousel_rebuild.html.j2",
        "_w": STARTUP_SLIDE_W, "_h": STARTUP_SLIDE_H,
        "_out_suffix": f"slide{total:02d}-rebuild",
        "rebuild_headline": rebuild_headline,
        "rebuild_idea": rebuild_idea,
        "rebuild_stack": rebuild_stack,
        "rebuild_monetization": rebuild_monetization,
        "ring_data_uri": ring_data_uri,
        "index": f"{total:02d}", "total": f"{total:02d}",
    })

    slug = project_name.lower().replace(" ", "-")
    return {"slug": slug, "telegram": telegram_ctx, "slides": slides}


def render_startup_package(pkg: dict, out_dir: Path) -> list:
    """Рендерит telegram-обложку + все 7 слайдов карусели. Возвращает список путей."""
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    out_dir.mkdir(parents=True, exist_ok=True)
    slug = pkg["slug"]
    out_paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # telegram-обложка
        html = env.get_template("startup_telegram_1080x1080.html.j2").render(**pkg["telegram"])
        page = browser.new_page(viewport={"width": STARTUP_TELEGRAM_W, "height": STARTUP_TELEGRAM_H}, device_scale_factor=2)
        page.set_content(html, wait_until="networkidle")
        out_path = out_dir / f"{slug}-telegram.png"
        page.locator(".card").screenshot(path=str(out_path))
        page.close()
        out_paths.append(out_path)
        print(f"  ✓ {out_path}")

        # 7 слайдов карусели
        for slide in pkg["slides"]:
            html = env.get_template(slide["_template"]).render(**slide)
            page = browser.new_page(viewport={"width": slide["_w"], "height": slide["_h"]}, device_scale_factor=2)
            page.set_content(html, wait_until="networkidle")
            out_path = out_dir / f"{slug}-{slide['_out_suffix']}.png"
            page.locator(".card").screenshot(path=str(out_path))
            page.close()
            out_paths.append(out_path)
            print(f"  ✓ {out_path}")

        browser.close()

    return out_paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Генератор карточек 円相 (Enso).")
    parser.add_argument("repo", nargs="?", default=None,
                        help="owner/repo (или ссылка на GitHub) — режим одиночной карточки с данными из GitHub (только --type github)")
    parser.add_argument("--type", dest="card_type", choices=["github", "startup"], default="github",
                        help="тип карточки: github (проект, нужно фото) или startup (карусель разбора одного провалившегося стартапа, фото не нужно)")
    parser.add_argument("--json", dest="json_path", default=None,
                        help="путь к .json с ОДНИМ объектом (для type=github — одиночная карточка; для type=startup — единственный поддерживаемый режим)")
    parser.add_argument("--batch", dest="batch_path", default=None,
                        help="путь к .json со СПИСКОМ объектов — подборка карточек (только type=github)")
    parser.add_argument("--json-example", action="store_true",
                        help="напечатать пример JSON (для --json, с учётом --type) и выйти")
    parser.add_argument("--photo", help="фото/скриншот — для --json или repo (только --type github)")
    parser.add_argument("--photos", nargs="+", default=None,
                        help="фото по порядку элементов — для --batch (только --type github; кол-во должно совпадать со списком)")
    parser.add_argument("--index", type=int, default=1, help="номер карточки (одиночная, по умолчанию 1; в подборке не используется; не используется для --type startup)")
    parser.add_argument("--total", type=int, default=1, help="всего карточек (одиночная, по умолчанию 1; в подборке не используется; не используется для --type startup)")
    parser.add_argument("--out", default="output", help="папка для результатов (по умолчанию ./output)")
    args = parser.parse_args()

    if args.json_example:
        example = MANUAL_JSON_EXAMPLE_STARTUP if args.card_type == "startup" else MANUAL_JSON_EXAMPLE
        print(json.dumps(example, ensure_ascii=False, indent=2))
        return

    if not RING_ASSET.exists():
        sys.exit(f"Не найден ассет кольца: {RING_ASSET}")

    out_dir = Path(args.out)

    # ---- type = startup: единственный режим — один JSON, весь пакет ----
    if args.card_type == "startup":
        if args.repo or args.batch_path or args.photo or args.photos:
            sys.exit("--type startup поддерживает только --json — нет режима repo/--batch/--photo(s), "
                      "здесь один стартап = один пакет из 8 картинок (обложка + 7 слайдов карусели).")
        if not args.json_path:
            sys.exit("--type startup требует --json path/to/data.json. Пример: python generate.py --type startup --json-example")
        json_path = Path(args.json_path)
        if not json_path.exists():
            sys.exit(f"JSON-файл не найден: {json_path}")
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            sys.exit(f"Невалидный JSON в {json_path}: {e}")
        try:
            pkg = build_startup_package(data)
        except ManualDataError as e:
            sys.exit(str(e))
        print(f"→ Рендерю пакет «{pkg['slug']}»: обложка + {N_TOTAL_SLIDES} слайдов карусели…")
        render_startup_package(pkg, out_dir)
        print("Готово ✅")
        return

    # ---- type = github ----
    modes_given = sum(bool(x) for x in (args.repo, args.json_path, args.batch_path))
    if modes_given != 1:
        sys.exit("Укажите ровно один источник данных: repo (GitHub), --json (одна карточка) или --batch (подборка). "
                 "Пример JSON: python generate.py --json-example")

    # ---- подборка: список объектов + список фото по порядку ----
    if args.batch_path:
        if not args.photos:
            sys.exit("Нужен --photos photo1.png photo2.png ... (по одному на каждый элемент подборки).")
        batch_path = Path(args.batch_path)
        if not batch_path.exists():
            sys.exit(f"JSON-файл не найден: {batch_path}")
        try:
            items = parse_batch_items(batch_path.read_text(encoding="utf-8"))
        except ManualDataError as e:
            sys.exit(str(e))

        photo_paths = [Path(p) for p in args.photos]
        for p in photo_paths:
            if not p.exists():
                sys.exit(f"Файл не найден: {p}")
        if len(photo_paths) != len(items):
            sys.exit(f"Фото ({len(photo_paths)}) и элементов в подборке ({len(items)}) должно быть поровну.")

        total = len(items)
        print(f"→ Рендерю подборку из {total} карточек…")
        for i, (item, photo_path) in enumerate(zip(items, photo_paths), start=1):
            try:
                context = build_context_manual(item, photo_path, index=i, total=total, is_batch=True)
            except ManualDataError as e:
                sys.exit(f"Элемент №{i}: {e}")
            slug = f"{i:02d}-{context['project_name'].lower().replace(' ', '-')}"
            print(f"  [{i}/{total}] {context['project_name']}")
            render_all(context, out_dir, slug)
        print("Готово ✅")
        return

    # ---- одиночная карточка: --json или repo (GitHub) ----
    if not args.photo:
        sys.exit("Нужен --photo path/to/screenshot.png")
    photo_path = Path(args.photo)
    if not photo_path.exists():
        sys.exit(f"Файл не найден: {photo_path}")

    if args.json_path:
        json_path = Path(args.json_path)
        if not json_path.exists():
            sys.exit(f"JSON-файл не найден: {json_path}")
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            sys.exit(f"Невалидный JSON в {json_path}: {e}")
        try:
            context = build_context_manual(data, photo_path, args.index, args.total, is_batch=False)
        except ManualDataError as e:
            sys.exit(str(e))
        slug = context["project_name"].lower().replace(" ", "-")
    else:
        print(f"→ Загружаю данные {args.repo} с GitHub…")
        try:
            repo_data = fetch_repo(args.repo)
        except RepoFetchError as e:
            sys.exit(str(e))
        context = build_context(repo_data, photo_path, args.index, args.total, is_batch=False)
        slug = repo_data["name"].lower().replace(" ", "-")

    print("→ Рендерю 3 формата (story / post / telegram)…")
    render_all(context, out_dir, slug)

    print("Готово ✅")


if __name__ == "__main__":
    main()
