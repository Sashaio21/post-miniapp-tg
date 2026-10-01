"""Загрузка пакетов шаблонов и рендер карточек (HTML для превью, PNG через Playwright)."""
import json
import re
from pathlib import Path
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader

from generate import (
    RING_ASSET,
    ManualDataError,
    build_context_manual,
    build_startup_package,
    to_data_uri,
)

PACKS_DIR = Path(__file__).parent / "packs"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,48}$")
PREVIEW_MAX = 8  # максимум карточек в превью

PLACEHOLDER = "data:image/svg+xml;utf8," + quote(
    "<svg xmlns='http://www.w3.org/2000/svg' width='800' height='600'>"
    "<rect width='800' height='600' fill='#23221a'/>"
    "<text x='400' y='310' fill='#8a8676' font-size='40' text-anchor='middle' "
    "font-family='sans-serif'>Фото проекта</text></svg>")


# ------------------------------------------------------------- загрузка
def load_packs() -> dict:
    """Читает packs/*/template.json. Битые пакеты пропускаются."""
    found = {}
    if not PACKS_DIR.exists():
        return found
    for d in sorted(PACKS_DIR.iterdir()):
        mf = d / "template.json"
        if not (d.is_dir() and _ID_RE.match(d.name) and mf.exists()):
            continue
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            pack = {
                "id": d.name, "dir": d,
                "name": str(m.get("name", d.name)), "description": str(m.get("description", "")),
                "width": int(m["width"]), "height": int(m["height"]),
                "file": str(m.get("file", "template.html.j2")), "builder": m.get("builder"),
                "photo": m.get("photo"), "hint": str(m.get("hint", "")),
                "selector": str(m.get("selector", ".card")),
                "example": m.get("example", {}), "order": int(m.get("order", 100)),
            }
            if not (d / pack["file"]).is_file():
                continue
        except (ValueError, KeyError, TypeError, OSError):
            continue
        found[d.name] = pack
    return dict(sorted(found.items(), key=lambda kv: (kv[1]["order"], kv[1]["name"])))


# ------------------------------------------------------------- builders
def _r(tpl, ctx, w, h, label):
    return {"template": tpl, "ctx": ctx, "w": w, "h": h, "label": label}


def _items(data):
    items = data if isinstance(data, list) else [data]
    if not items or not all(isinstance(i, dict) for i in items):
        raise ManualDataError("Ожидается JSON-объект (или список объектов).")
    return items


def _b_default(pack, data, photo):
    items = _items(data)
    return [_r(pack["file"], it, pack["width"], pack["height"],
               pack["name"] if len(items) == 1 else f"{pack['name']} · {n}")
            for n, it in enumerate(items, start=1)]


def _b_github(pack, data, photo):
    items, out = _items(data), []
    for n, it in enumerate(items, start=1):
        ctx = build_context_manual(it, RING_ASSET, n, len(items), len(items) > 1)
        ctx["photo_data_uri"] = photo or PLACEHOLDER
        label = pack["name"] if len(items) == 1 else f"{ctx['project_name']}"
        out.append(_r(pack["file"], ctx, pack["width"], pack["height"], label))
    return out


def _b_startup_cover(pack, data, photo):
    if isinstance(data, list):
        raise ManualDataError("Для стартапа нужен один JSON-объект, не список.")
    return [_r(pack["file"], build_startup_package(data)["telegram"],
               pack["width"], pack["height"], pack["name"])]


def _b_startup_carousel(pack, data, photo):
    if isinstance(data, list):
        raise ManualDataError("Для стартапа нужен один JSON-объект, не список.")
    slides = build_startup_package(data)["slides"]
    return [_r(sl["_template"], sl, sl["_w"], sl["_h"], f"Слайд {i}/{len(slides)}")
            for i, sl in enumerate(slides, start=1)]


BUILDERS = {"github": _b_github, "startup_cover": _b_startup_cover, "startup_carousel": _b_startup_carousel}


def build_renders(pack: dict, data, photo=None) -> list:
    """JSON → список карточек [{template, ctx, w, h, label}]. Ошибки данных — ManualDataError."""
    name = pack["builder"]
    if name and name not in BUILDERS:
        raise ManualDataError(f"Неизвестный builder «{name}» в template.json")
    return BUILDERS.get(name, _b_default)(pack, data, photo)


# ------------------------------------------------------------- рендер
_envs = {}


def _env(pack: dict, preview: bool) -> Environment:
    key = (pack["id"], preview)
    if key not in _envs:
        env = Environment(loader=FileSystemLoader(str(pack["dir"])))  # без автоэкранирования: <b>/<span> в текстах
        assets = (pack["dir"] / "assets").resolve()

        def asset(name):
            f = (assets / str(name)).resolve()
            if assets not in f.parents or not f.is_file():
                return ""
            return f"/packs/{pack['id']}/assets/{name}" if preview else to_data_uri(f)

        env.globals["asset"] = asset
        _envs[key] = env
    return _envs[key]


_RING_URI = to_data_uri(RING_ASSET) if RING_ASSET.exists() else None


def render_html(pack: dict, r: dict, preview: bool = False) -> str:
    html = _env(pack, preview).get_template(r["template"]).render(**r["ctx"])
    if preview and _RING_URI:  # 260 КБ base64 → короткая ссылка, браузер кэширует
        html = html.replace(_RING_URI, "/assets/" + RING_ASSET.name)
    return html


def render_pngs(pack: dict, renders: list, out_dir: Path) -> list:
    from playwright.sync_api import sync_playwright
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for i, r in enumerate(renders, start=1):
            page = browser.new_page(viewport={"width": r["w"], "height": r["h"]}, device_scale_factor=2)
            page.set_content(render_html(pack, r, False), wait_until="networkidle")
            out = out_dir / f"{pack['id']}-{i:02d}.png"
            el = page.locator(pack["selector"])
            if el.count():
                el.first.screenshot(path=str(out))
            else:
                page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": r["w"], "height": r["h"]})
            page.close()
            paths.append(out)
        browser.close()
    return paths
