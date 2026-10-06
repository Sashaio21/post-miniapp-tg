"""Общие хелперы и сборка контекстов карточек (используется app.py и packs.py)."""
import base64
import mimetypes
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = BASE_DIR / "assets"
RING_ASSET = ASSETS_DIR / "enso-ring-transparent.png"

# Карусель провала стартапа: обложка + 5 контентных слайдов + ребилд = 7 слайдов
STARTUP_SLIDE_W, STARTUP_SLIDE_H = 1080, 1350
N_CONTENT_SLIDES = 5
N_TOTAL_SLIDES = N_CONTENT_SLIDES + 2


class ManualDataError(Exception):
    """Некорректный JSON с данными карточки."""


def to_data_uri(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    mime = mime or "application/octet-stream"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


# Кольцо кодируется один раз при старте, а не при каждом запросе
RING_URI = to_data_uri(RING_ASSET) if RING_ASSET.exists() else ""


def format_stars(n: int) -> str:
    """22500 -> '22.5k', 850 -> '850'."""
    if n >= 1000:
        value = f"{n / 1000:.1f}".rstrip("0").rstrip(".")
        return f"{value}k"
    return str(n)


def build_tagline(description: str, limit: int = 90) -> str:
    """Короткая версия описания под тег-лайн, если полное описание длинное."""
    description = description.strip()
    if len(description) <= limit:
        return description
    truncated = description[: limit - 1].rsplit(" ", 1)[0]
    return truncated + "…"


def build_context_manual(data: dict, index: int, total: int, is_batch: bool) -> dict:
    """
    Контекст GitHub-карточки из готового JSON. Обязательное поле — project_name.
    "stars" — число (22500 -> "22.5k") или готовая строка. "index"/"total" в JSON
    перекрывают переданные. is_batch: True — футер «подборка NN/NN», False — линия.
    Фото (photo_data_uri) добавляет вызывающий код.
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
        "ring_data_uri": RING_URI,
        "index": f"{idx:02d}",
        "total": f"{tot:02d}",
        "is_batch": is_batch,
    }


def _req_str(data: dict, key: str, where: str = "") -> str:
    val = str(data.get(key) or "").strip()
    if not val:
        raise ManualDataError(f"В JSON не хватает обязательного поля «{key}»{where}.")
    return val


def build_startup_package(data: dict) -> dict:
    """
    Проверяет JSON одного стартапа и раскладывает на контексты:
    {"telegram": ctx, "slides": [cover, 5 × content, rebuild]}.
    Обязательные поля: project_name, stat_num, hook_headline, slides (ровно 5),
    rebuild (idea и stack обязательны, monetization — нет).
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

    total = N_TOTAL_SLIDES
    size = {"_w": STARTUP_SLIDE_W, "_h": STARTUP_SLIDE_H}

    telegram_ctx = {
        "project_name": project_name,
        "tagline": tagline,
        "stat_num": stat_num,
        "stat_label": stat_label,
        "flag": flag,
        "years": years,
        "sector": sector,
        "ring_data_uri": RING_URI,
    }

    slides = [{
        "_template": "startup_carousel_cover.html.j2", **size,
        "stat_num": stat_num,
        "stat_label": stat_label,
        "hook_headline": hook_headline,
        "hook_subhead": hook_subhead,
        "ring_data_uri": RING_URI,
        "index": f"{1:02d}", "total": f"{total:02d}",
    }]

    for i, item in enumerate(raw_slides, start=2):
        if not isinstance(item, dict):
            raise ManualDataError(f"Элемент №{i - 1} в «slides» — не объект (словарь).")
        tag = _req_str(item, "tag", f" (slides[{i - 2}])")
        headline = _req_str(item, "headline", f" (slides[{i - 2}])")
        body = item.get("body") or []
        if not isinstance(body, list) or not all(isinstance(p, str) for p in body):
            raise ManualDataError(f"«body» в slides[{i - 2}] должен быть списком строк-абзацев.")
        slides.append({
            "_template": "startup_carousel_content.html.j2", **size,
            "tag": tag,
            "headline": headline,
            "body": body,
            "meta": str(item.get("meta") or "").strip(),
            "ring_data_uri": RING_URI,
            "index": f"{i:02d}", "total": f"{total:02d}",
        })

    slides.append({
        "_template": "startup_carousel_rebuild.html.j2", **size,
        "rebuild_headline": rebuild_headline,
        "rebuild_idea": rebuild_idea,
        "rebuild_stack": rebuild_stack,
        "rebuild_monetization": rebuild_monetization,
        "ring_data_uri": RING_URI,
        "index": f"{total:02d}", "total": f"{total:02d}",
    })

    return {"telegram": telegram_ctx, "slides": slides}
