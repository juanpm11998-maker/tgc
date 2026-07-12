#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agente TGC - actualiza precios/imagenes, calcula EV de gradeo, regenera la galeria HTML
y (opcional) escanea sets enteros para proponer nuevas cartas.

Solo stdlib (urllib, json). No necesita pip install.

Uso:
  python update.py                      -> refresca watchlist y regenera index.html
  python update.py --dry-run            -> usa solo precios semilla (sin API)
  python update.py --add "Ace OP16" --game onepiece   -> anade una carta a mano
  python update.py --scan               -> escanea scan_sets, escribe candidates.json + candidates.html
  python update.py --scan --autoadd     -> ademas mete las mejores candidatas en la watchlist
  python update.py --scan --mock        -> prueba el scan con datos de ejemplo (sin API)

Env (opcionales): TCG_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
"""

import os, re, json, time, argparse, datetime, urllib.request, urllib.parse, urllib.error, html as htmlmod

HERE = os.path.dirname(os.path.abspath(__file__))
WATCHLIST = os.path.join(HERE, "watchlist.json")
OUTPUT_HTML = os.path.join(HERE, "index.html")
CAND_JSON = os.path.join(HERE, "candidates.json")
CAND_HTML = os.path.join(HERE, "candidates.html")

# ----- Ajusta si cambian los endpoints/campos de tu API -----
API_BASE = "https://api.tcgpricelookup.com/v1"

# Registro de juegos: cualquier TCG. Cada uno define:
#   api   -> slug que espera la API de precios
#   label -> nombre visible en la galeria
#   theme -> clave de estilo CSS para el placeholder (ver HEAD)
# Anade el que quieras aqui o desde watchlist.json > config > games (se fusiona).
# slugs 'api' segun /v1/games de tcgpricelookup (jul-2026):
# pokemon, pokemon-jp, onepiece, mtg, yugioh, lorcana, fab, swu
GAMES = {
    "pokemon_en": {"api": "pokemon",    "label": "Pokemon EN",      "theme": "pk"},
    "pokemon_jp": {"api": "pokemon-jp", "label": "Pokemon JP",      "theme": "pk"},
    "onepiece":   {"api": "onepiece",   "label": "One Piece",       "theme": "op"},
    "magic":      {"api": "mtg",        "label": "Magic",           "theme": "mtg"},
    "yugioh":     {"api": "yugioh",     "label": "Yu-Gi-Oh!",       "theme": "ygo"},
    "lorcana":    {"api": "lorcana",    "label": "Lorcana",         "theme": "lor"},
    "fab":        {"api": "fab",        "label": "Flesh and Blood", "theme": "gn"},
    "starwars":   {"api": "swu",        "label": "Star Wars U.",    "theme": "gn"},
}
# ------------------------------------------------------------

# gem_rate estimado por juego+rareza. Las claves son NOMBRES que devuelve la API
# (ej. "SPECIAL ART RARE") y tambien codigos cortos; se elige la clave mas larga que
# encaje (asi "SPECIAL ART RARE" gana a "ART RARE" o "RARE"). Ajusta con GemRate/PSA pop.
GEM_DEFAULTS = {
    "pokemon_jp": {"SPECIAL ILLUSTRATION RARE": 0.80, "SPECIAL ART RARE": 0.80,
                   "ILLUSTRATION RARE": 0.60, "ART RARE": 0.60, "MEGA ULTRA RARE": 0.78,
                   "ULTRA RARE": 0.72, "DOUBLE RARE": 0.60, "HYPER RARE": 0.65,
                   "SAR": 0.80, "SIR": 0.80, "AR": 0.60, "UR": 0.70, "_": 0.62},
    "pokemon_en": {"SPECIAL ILLUSTRATION RARE": 0.45, "ILLUSTRATION RARE": 0.42,
                   "ULTRA RARE": 0.42, "DOUBLE RARE": 0.40, "HYPER RARE": 0.42,
                   "SIR": 0.45, "SAR": 0.45, "AR": 0.42, "_": 0.40},
    "onepiece":   {"SECRET RARE": 0.12, "SUPER RARE": 0.18, "MANGA": 0.10, "LEADER": 0.20,
                   "SPECIAL CARD": 0.12, "MR": 0.10, "SEC": 0.12, "L": 0.20, "SR": 0.18, "_": 0.15},
    "magic":      {"MYTHIC": 0.35, "RARE": 0.35, "_": 0.35},
    "yugioh":     {"GHOST RARE": 0.20, "SECRET RARE": 0.30, "ULTRA RARE": 0.35,
                   "GHOST": 0.20, "SECRET": 0.30, "ULTRA": 0.35, "_": 0.30},
    "lorcana":    {"ENCHANTED": 0.45, "LEGENDARY": 0.45, "_": 0.42},
}


def game_meta(game):
    """Metadatos del juego; degrada con elegancia para juegos no registrados."""
    return GAMES.get(game, {"api": game, "label": game.replace("_", " ").title(), "theme": "gn"})


# Moneda de presentacion. La fuente de verdad (watchlist/history) sigue en USD;
# aqui solo convertimos para MOSTRAR. Config: watchlist.json > config > display.
DISPLAY = {"symbol": "$", "fx": 1.0}
BANDS = {"lo": 50, "hi": 100}   # umbrales de banda, en moneda de presentacion


def money(v):
    """Formatea un importe USD a la moneda de presentacion (con separador de miles)."""
    return f'{DISPLAY["symbol"]}{(v or 0) * DISPLAY["fx"]:,.0f}'


def disp(v):
    """Valor numerico en moneda de presentacion (para atributos data-* y bandas)."""
    return round((v or 0) * DISPLAY["fx"])


def load():
    with open(WATCHLIST, "r", encoding="utf-8") as f:
        return json.load(f)


def save(data):
    with open(WATCHLIST, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# La API va tras Cloudflare: sin User-Agent de navegador devuelve 403 (error 1010).
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


def http_json(url, headers=None, timeout=25, retries=3):
    h = {"User-Agent": UA, "Accept": "application/json"}
    h.update(headers or {})
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=h)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:   # limite por rafaga: espera y reintenta
                time.sleep(2 + attempt * 2); continue
            raise


def dig(d, *paths):
    for p in paths:
        cur = d; ok = True
        for k in p.split("."):
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                ok = False; break
        if ok and cur not in (None, "", 0):
            return cur
    return None


def parse_item(item):
    """Extrae raw, psa10, imagen, nombre, rareza, set, codigo de un item de la API.
    Rutas segun la respuesta real de tcgpricelookup (jul-2026). El PSA10 solo existe
    en planes de pago (prices.graded.*); en el plan gratis viene ausente -> None."""
    raw = dig(item, "prices.raw.near_mint.tcgplayer.market",
              "prices.raw.near_mint.tcgplayer.low",
              "prices.raw.near_mint.ebay.avg_7d",
              "prices.raw.lightly_played.tcgplayer.market",
              # compat con MOCK / otras formas
              "prices.market_price", "market_price")
    psa10 = dig(item, "prices.graded.psa.10.ebay.avg_7d",
                "prices.graded.psa.10.tcgplayer.market",
                "prices.graded.psa.10", "prices.psa10")
    img = dig(item, "image_url", "image", "images.large", "images.small")
    name = dig(item, "name_numbered", "name", "title")
    rarity = dig(item, "rarity", "rarity_code")
    setname = dig(item, "set.name", "episode.name", "set_name", "set")
    code = dig(item, "number", "card_number", "id")
    url = dig(item, "url", "detail_url", "product_url", "permalink")   # esta API no da URL de ficha
    return {
        "raw": float(raw) if raw else None,
        "psa10": float(psa10) if psa10 else None,
        "image_url": img, "name": name, "rarity": rarity,
        "set": setname, "code": code, "url": url,
    }


def items_from(data):
    if isinstance(data, dict):
        for k in ("data", "results", "cards", "items"):
            if isinstance(data.get(k), list):
                return data[k]
        return [data]
    if isinstance(data, list):
        return data
    return []


# --------------------------- watchlist refresh ---------------------------

def num_primary(n):
    """Numero principal de una carta: '212/193' -> '212', '080' -> '80'."""
    if not n:
        return None
    s = re.split(r"[/\s]", str(n))[0].strip().lstrip("0")
    return s or None


def name_overlap(a, b):
    """Nº de palabras (>=3 letras) en comun entre dos nombres."""
    wa = {w for w in re.findall(r"[a-z0-9]+", (a or "").lower()) if len(w) >= 3}
    wb = {w for w in re.findall(r"[a-z0-9]+", (b or "").lower()) if len(w) >= 3}
    return len(wa & wb)


def choose_match(items, name, target_num):
    """Elige la carta correcta de los resultados de busqueda.
    Prioriza casar por NUMERO de carta (evita cajas de sobres y variantes baratas).
    Sin numero objetivo, no arriesga: devuelve None (mantiene el seed)."""
    # descarta sellado / no-cartas: sin rareza y sin numero = probable caja/producto
    cards = [it for it in items if it.get("rarity") or num_primary(it.get("code"))]
    if not cards:
        return None
    if target_num:
        hits = [it for it in cards if num_primary(it.get("code")) == target_num]
        if not hits:
            return None
        hits.sort(key=lambda it: name_overlap(it.get("name"), name), reverse=True)
        return hits[0]
    return None   # sin numero fiable, no sobreescribimos datos buenos con una adivinanza


def fetch_live(card, api_key):
    if not api_key:
        return None
    game = game_meta(card["game"])["api"]
    q = urllib.parse.quote(card.get("query") or card["name"])
    url = f"{API_BASE}/cards/search?game={game}&q={q}&limit=100"
    # numero objetivo: campo 'number' explicito si existe; si no, se extrae del set/nombre
    target = num_primary(card.get("number") or card_number(card.get("set"), card.get("query"), card.get("name")))
    try:
        data = http_json(url, headers={"X-API-Key": api_key})
        items = [parse_item(x) for x in items_from(data)]
        p = choose_match(items, card["name"], target)
        if not p:
            return None
        out = {"_found": True}
        if p["raw"]:   out["raw"] = p["raw"]
        if p["psa10"]: out["psa10"] = p["psa10"]
        if p["image_url"]: out["image_url"] = p["image_url"]
        if p["url"]:   out["url"] = p["url"]
        return out
    except Exception as e:
        print(f"  ! API fallo para {card['name']}: {e}")
        return None


def ev_metrics(raw, psa10, gem, fee, rec):
    """Nucleo del calculo. Devuelve EV neto, ROI%, multiplicador y gem de equilibrio.

    EV        = gem*PSA10 + (1-gem)*(rec*raw) - raw - fee   (beneficio esperado en USD)
    ROI       = EV / coste, con coste = raw + fee            (rentabilidad sobre lo invertido)
    gem_be    = gem_rate que hace EV=0 (break-even).         (tu margen de seguridad)
                margen = gem - gem_be; si es negativo, pierdes en media.
    """
    if not raw or not psa10:
        return {"raw": raw or 0, "psa10": psa10 or 0, "gap": 0, "mult": 0,
                "ev": -9999, "roi": 0, "gem_be": None, "margin": 0, "buy": False}
    cost = raw + fee
    ev = gem * psa10 + (1 - gem) * (rec * raw) - raw - fee
    denom = psa10 - rec * raw           # >0 si el 10 vale mas que la recuperacion del raw
    gem_be = (raw * (1 - rec) + fee) / denom if denom > 0 else None
    return {
        "raw": raw, "psa10": psa10, "gap": psa10 - raw,
        "mult": round(psa10 / raw, 1) if raw else 0,
        "ev": round(ev),
        "roi": round(ev / cost * 100) if cost else 0,
        "gem_be": gem_be,
        "margin": round((gem - gem_be) * 100) if gem_be is not None else None,
    }


def metrics(card, cfg):
    raw = card.get("live_raw") or card["seed_raw"]
    psa10 = card.get("live_psa10") or card["seed_psa10"]
    gem = card["gem_rate"]; fee = card["grading_fee"]
    # recovery_frac por carta si existe (p.ej. texturas que se rayan = menor), si no el global
    rec = card.get("recovery_frac", cfg.get("recovery_frac", 0.85))
    m = ev_metrics(raw, psa10, gem, fee, rec)
    m["buy"] = m["ev"] >= cfg.get("buy_threshold_ev", 30)
    return m


def refresh(data, dry_run):
    api_key = os.environ.get("TCG_API_KEY", "").strip()
    today = datetime.date.today().isoformat()
    for c in data["cards"]:
        live = None if dry_run else fetch_live(c, api_key)
        if live:
            # Solo actualizamos precios en vivo si tenemos AMBOS (raw y PSA10). El plan gratis
            # no da PSA10: actualizar solo el raw desincronizaria el par y romperia el EV, asi
            # que mantenemos tu par seed (tus valores investigados) y solo refrescamos la imagen.
            if "raw" in live and "psa10" in live:
                c["live_raw"] = live["raw"]; c["live_psa10"] = live["psa10"]
            else:
                c.pop("live_raw", None); c.pop("live_psa10", None)
            # match fiable (por numero): adoptamos su imagen aunque hubiera otra (corrige malas)
            if live.get("image_url"):
                c["image_url"] = live["image_url"]
            # auto-corregir el enlace: si la API da URL directa y el actual es de grupo, adoptarla
            if live.get("url") and not is_group_link(live["url"]) and is_group_link(c.get("link")):
                c["link"] = live["url"]
            c.pop("unverified", None)
        elif not dry_run and api_key:
            # habia clave pero no hubo match fiable: dejamos el seed (sin corromper).
            # Solo marcamos REVISAR si la carta TIENE numero y aun asi no aparece (posible
            # ficha fantasma); si no tiene numero, no se puede verificar y no es culpa suya.
            c.pop("live_raw", None); c.pop("live_psa10", None)
            has_num = num_primary(c.get("number") or card_number(c.get("set"), c.get("query"), c.get("name")))
            if has_num:
                c["unverified"] = True
                print(f"  ? tiene #{has_num} pero no casa en API: revisa '{c['name']}'")
            else:
                c.pop("unverified", None)
        else:
            # sin clave (o dry-run) no se puede verificar: no marcamos REVISAR
            c.pop("unverified", None)
        # afinar el enlace: de grupo/set -> busqueda del ejemplar concreto (respeta directos)
        c["link"] = best_link(c["name"], c.get("query"), c.get("set"), c.get("link"))
        m = metrics(c, data["config"])
        hist = c.setdefault("history", [])
        if not hist or hist[-1].get("date") != today:
            hist.append({"date": today, "raw": m["raw"], "psa10": m["psa10"], "ev": m["ev"]})
        # dedup por fecha (los merges pueden dejar duplicados): una entrada por dia, ordenada
        by_date = {}
        for h in hist:
            by_date[h.get("date")] = h
        c["history"] = [by_date[d] for d in sorted(by_date)][-90:]
        c["_m"] = m
    return data


# ----------------------------- SCAN -----------------------------

def rarity_lookup(table, rarity, default):
    """Devuelve el valor de la clave MAS LARGA que sea subcadena de la rareza.
    Asi 'SPECIAL ART RARE' encaja con esa clave y no con 'ART RARE' o 'RARE'."""
    if not rarity:
        return default
    key = str(rarity).upper()
    best = None
    for k, v in table.items():
        if k != "_" and k in key and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best[1] if best else table.get("_", default)


def estimate_gem(game, rarity):
    table = GEM_DEFAULTS.get(game, {"_": 0.30})
    return rarity_lookup(table, rarity, table.get("_", 0.30))


def estimate_fee(psa10):
    if psa10 < 499: return 25
    if psa10 < 999: return 33
    return 50


# Multiplo PSA10/raw por juego+rareza. Solo se usa para ESTIMAR el PSA10 cuando la
# fuente de precios no lo da (tier gratis). Es una aproximacion tosca por rareza:
# sirve para DESCUBRIR candidatas, no para fiarte del EV. Verifica el 10 real antes.
PSA10_MULT_DEFAULTS = {
    "pokemon_jp": {"SPECIAL ILLUSTRATION RARE": 2.5, "SPECIAL ART RARE": 2.5,
                   "ILLUSTRATION RARE": 3.2, "ART RARE": 3.2, "MEGA ULTRA RARE": 2.6,
                   "ULTRA RARE": 2.2, "DOUBLE RARE": 1.8, "HYPER RARE": 2.2,
                   "SAR": 2.5, "SIR": 2.5, "AR": 3.2, "UR": 2.2, "_": 2.3},
    "pokemon_en": {"SPECIAL ILLUSTRATION RARE": 2.2, "ILLUSTRATION RARE": 2.6,
                   "ULTRA RARE": 2.0, "DOUBLE RARE": 1.8, "HYPER RARE": 2.0,
                   "SIR": 2.2, "SAR": 2.2, "AR": 2.6, "_": 2.0},
    "onepiece":   {"SECRET RARE": 4.5, "SUPER RARE": 2.6, "MANGA": 5.0, "LEADER": 2.5,
                   "SPECIAL CARD": 4.0, "MR": 5.0, "SEC": 4.5, "L": 2.5, "SR": 2.6, "_": 3.0},
    "magic":      {"MYTHIC": 2.0, "RARE": 2.0, "_": 2.0},
    "yugioh":     {"GHOST RARE": 3.5, "SECRET RARE": 3.0, "ULTRA RARE": 2.3,
                   "GHOST": 3.5, "SECRET": 3.0, "ULTRA": 2.3, "_": 2.6},
    "lorcana":    {"ENCHANTED": 2.6, "LEGENDARY": 2.4, "_": 2.3},
}


def estimate_psa10(game, rarity, raw):
    """Estima el PSA10 a partir del raw real cuando la API no da el graded (tier gratis)."""
    table = PSA10_MULT_DEFAULTS.get(game, {"_": 2.3})
    return round(raw * rarity_lookup(table, rarity, table.get("_", 2.3)), 2)


MOCK_SET = [
    {"name_numbered": "Sabo OP08-118", "rarity": "MR", "card_number": "OP08-118",
     "episode": {"name": "OP-08"}, "image": "https://images.tcggo.com/x/sabo.webp",
     "prices": {"market_price": 140, "psa10": 900}},
    {"name_numbered": "Nami OP08-041", "rarity": "SR", "card_number": "OP08-041",
     "episode": {"name": "OP-08"}, "image": "https://images.tcggo.com/x/nami08.webp",
     "prices": {"market_price": 12, "psa10": 22}},
    {"name_numbered": "Yamato OP08-119", "rarity": "MR", "card_number": "OP08-119",
     "episode": {"name": "OP-08"}, "image": "https://images.tcggo.com/x/yamato.webp",
     "prices": {"market_price": 60, "psa10": 480}},
    {"name_numbered": "Zoro OP08-006", "rarity": "C", "card_number": "OP08-006",
     "episode": {"name": "OP-08"}, "prices": {"market_price": 0.2, "psa10": 8}},
]


def fetch_set(game_key, set_code, api_key, max_pages, mock=False):
    """Lista las cartas de un set via /cards/search?game=&set=&limit=&offset= (paginado)."""
    if mock:
        return [parse_item(x) for x in MOCK_SET]
    game = game_meta(game_key)["api"]
    out = []
    limit = 100
    for page in range(max_pages):
        offset = page * limit
        url = (f"{API_BASE}/cards/search?game={game}"
               f"&set={urllib.parse.quote(set_code)}&limit={limit}&offset={offset}")
        try:
            data = http_json(url, headers={"X-API-Key": api_key})
        except Exception as e:
            print(f"  ! scan fallo {game_key}/{set_code} offset{offset}: {e}")
            break
        its = items_from(data)
        if not its:
            break
        out.extend(parse_item(x) for x in its)
        total = data.get("total") if isinstance(data, dict) else None
        if len(its) < limit or (total is not None and offset + limit >= total):
            break
    return out


def norm(s):
    return "".join(ch.lower() for ch in (s or "") if ch.isalnum())


def card_number(*fields):
    """Extrae el identificador de la carta (para afinar la busqueda al ejemplar exacto)."""
    text = " ".join(str(x) for x in fields if x)
    for pat in (r'[A-Z]{1,4}\d{1,3}-\d{2,3}',   # OP01-016, SV8a-212
                r'#\s*(\d{1,3})',                # #212
                r'\b(\d{1,3})/\d{1,3}\b'):       # 173/165
        m = re.search(pat, text)
        if m:
            return m.group(1) if m.groups() else m.group(0)
    return None


def is_group_link(url):
    """True si el enlace lleva a un grupo de cartas (busqueda o set entero), no a una."""
    u = url or ""
    return (not u) or ("search-products" in u) or ("/console/" in u)


def best_link(name, query=None, extra=None, existing=None):
    """Devuelve el mejor enlace: respeta uno directo (/game/...); si no, construye una
    busqueda lo mas estrecha posible añadiendo el numero de carta para caer en el ejemplar."""
    if existing and not is_group_link(existing):
        return existing                       # ya apunta a la carta concreta: no lo toques
    base = query or name or ""
    num = card_number(name, query, extra)
    q = f"{base} {num}" if num and norm(num) not in norm(base) else base
    return f"https://www.pricecharting.com/search-products?q={urllib.parse.quote(q)}&type=prices"


def scan(data, api_key, autoadd, mock=False):
    cfg = data["config"]
    sc = cfg.get("scan", {})
    rec = cfg.get("recovery_frac", 0.85)
    min_raw = sc.get("min_raw", 5); max_raw = sc.get("max_raw", 400)
    min_mult = sc.get("min_mult", 3); min_ev = sc.get("min_ev", 40)
    autoadd_ev = sc.get("autoadd_ev", 120); max_pages = sc.get("max_pages_per_set", 3)
    top_n = sc.get("max_candidates", 40)
    sets = sc.get("sets", [])
    if mock and not sets:
        sets = [{"game": "onepiece", "code": "OP-08"}]

    existing = {norm(c["name"]) for c in data["cards"]} | {norm(c.get("id")) for c in data["cards"]}
    seen = set()
    cands = []
    for s in sets:
        gk = s["game"]; code = s["code"]
        print(f"  escaneando {gk} / {code} ...")
        for it in fetch_set(gk, code, api_key, max_pages, mock=mock):
            raw, psa10 = it["raw"], it["psa10"]
            if not raw:
                continue
            if raw < min_raw or raw > max_raw:
                continue
            # tier gratis no da PSA10: lo estimamos por rareza (a verificar)
            psa10_est = False
            if not psa10:
                psa10 = estimate_psa10(gk, it["rarity"], raw)
                psa10_est = True
            if not psa10:
                continue
            mult = psa10 / raw
            if mult < min_mult:
                continue
            gem = estimate_gem(gk, it["rarity"])
            fee = estimate_fee(psa10)
            em = ev_metrics(raw, psa10, gem, fee, rec)
            if em["ev"] < min_ev:
                continue
            nm = it["name"] or code
            if norm(nm) in existing or norm(nm) in seen:
                continue
            seen.add(norm(nm))
            cands.append({
                "id": norm(nm)[:40] or code, "name": nm, "game": gk,
                "set": it["set"] or code, "rarity": it["rarity"], "code": it["code"],
                "raw": round(raw, 2), "psa10": round(psa10, 2), "gap": round(psa10 - raw),
                "mult": round(mult, 1), "gem_est": gem, "fee": fee, "ev": em["ev"],
                "psa10_est": psa10_est,
                "roi": em["roi"], "gem_be": em["gem_be"], "margin": em["margin"],
                "image_url": it["image_url"],
                "link": it["url"] if (it.get("url") and not is_group_link(it["url"]))
                        else best_link(nm, nm, it.get("code") or it["set"] or code),
            })

    cands.sort(key=lambda c: c["ev"], reverse=True)
    cands = cands[:top_n]

    # marcar las que no estaban en el scan anterior (para el aviso de Telegram)
    prev_ids = set()
    try:
        with open(CAND_JSON, "r", encoding="utf-8") as f:
            prev_ids = {x.get("id") for x in json.load(f).get("candidates", [])}
    except Exception:
        pass
    for c in cands:
        c["is_new"] = c["id"] not in prev_ids

    with open(CAND_JSON, "w", encoding="utf-8") as f:
        json.dump({"generated": datetime.date.today().isoformat(), "candidates": cands}, f,
                  ensure_ascii=False, indent=2)
    render_candidates(cands)
    print(f"  {len(cands)} candidatas -> candidates.json / candidates.html")

    added = 0
    if autoadd:
        for c in cands:
            if c["ev"] < autoadd_ev or norm(c["name"]) in existing:
                continue
            data["cards"].append({
                "id": c["id"], "name": c["name"], "game": c["game"], "set": c["set"],
                "query": c["name"], "gem_rate": c["gem_est"], "grading_fee": c["fee"],
                "seed_raw": c["raw"], "seed_psa10": c["psa10"], "image_url": c["image_url"],
                "link": c["link"], "source": "scan", "pending_review": True,
                "note": (f"[scan] {c['rarity'] or ''} - revisa gem_rate"
                         + (" y PSA10 (ambos estimados)." if c.get("psa10_est") else " (estimado).")),
                "history": [],
            })
            existing.add(norm(c["name"])); added += 1
        if added:
            save(data)
        print(f"  autoadd: {added} cartas anadidas a la watchlist (pending_review=true).")
    return cands, added


# --------------------------- HTML ---------------------------

HEAD = """<!-- Generado por update.py -->
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cartas para grading - ranking por EV</title>
<style>
:root{--bg:#0e1116;--panel:#161b22;--panel2:#1c2230;--line:#2a3240;--txt:#e8edf4;--mut:#9aa7b8;--dim:#6b7789;--pk:#f0b429;--op:#e5484d;--up:#7ee787;--psa:#ffd76a;--ev:#58a6ff;--buy:#3fb950}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;line-height:1.5;padding:26px 16px 60px}
.wrap{max-width:1120px;margin:0 auto}
h1{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}
h2{font-size:17px;margin:34px 0 2px}
.meta{color:var(--mut);font-size:13px;margin:2px 0}
.disc{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:11px 13px;margin:14px 0 8px;font-size:12px;color:var(--mut)}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:15px;margin-top:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:14px;overflow:hidden;display:flex;flex-direction:column;position:relative;transition:transform .12s,border-color .12s}
.card:hover{transform:translateY(-3px);border-color:#3a4759}
.card.cand{border-style:dashed;opacity:.96}
.rank{position:absolute;top:8px;left:8px;z-index:2;background:rgba(0,0,0,.72);color:#fff;font-size:11px;font-weight:700;border-radius:20px;padding:2px 8px}
.buy{position:absolute;top:8px;right:8px;z-index:2;background:var(--buy);color:#04260f;font-size:10px;font-weight:800;border-radius:20px;padding:2px 8px}
.newf{position:absolute;top:8px;right:8px;z-index:2;background:#8957e5;color:#fff;font-size:10px;font-weight:800;border-radius:20px;padding:2px 8px}
.rev{position:absolute;top:8px;right:8px;z-index:2;background:#d29922;color:#241a00;font-size:10px;font-weight:800;border-radius:20px;padding:2px 8px}
.thumb{aspect-ratio:63/88;overflow:hidden;display:flex;align-items:center;justify-content:center}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.ph{width:100%;height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:9px;text-align:center;padding:12px}
.ph .g{font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;font-weight:700}
.ph .n{font-size:14px;font-weight:600;padding:0 6px}
.ph.pk{background:radial-gradient(120% 120% at 50% 0%,#3a2f0b 0%,#171a20 70%)}.ph.pk .g{color:var(--pk)}
.ph.op{background:radial-gradient(120% 120% at 50% 0%,#3a1113 0%,#171a20 70%)}.ph.op .g{color:#ff6b6f}
.ph.mtg{background:radial-gradient(120% 120% at 50% 0%,#2a1c3a 0%,#171a20 70%)}.ph.mtg .g{color:#c39bff}
.ph.ygo{background:radial-gradient(120% 120% at 50% 0%,#3a2405 0%,#171a20 70%)}.ph.ygo .g{color:#e6a23c}
.ph.lor{background:radial-gradient(120% 120% at 50% 0%,#0b2f3a 0%,#171a20 70%)}.ph.lor .g{color:#5fd0e0}
.ph.dgm{background:radial-gradient(120% 120% at 50% 0%,#0b243a 0%,#171a20 70%)}.ph.dgm .g{color:#6aa9ff}
.ph.gn{background:radial-gradient(120% 120% at 50% 0%,#26303a 0%,#171a20 70%)}.ph.gn .g{color:#9fb4c8}
/* barra de herramientas */
.toolbar{position:sticky;top:0;z-index:5;background:rgba(14,17,22,.92);backdrop-filter:blur(6px);border:1px solid var(--line);border-radius:12px;padding:10px 12px;margin:14px 0;display:flex;flex-wrap:wrap;gap:8px;align-items:center}
.toolbar input,.toolbar select{background:var(--panel2);color:var(--txt);border:1px solid var(--line);border-radius:8px;padding:6px 9px;font-size:13px}
.toolbar input{flex:1;min-width:150px}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:12px;color:var(--mut);background:var(--panel2);border:1px solid var(--line);border-radius:20px;padding:4px 11px;cursor:pointer;user-select:none}
.chip.on{color:#04260f;background:var(--buy);border-color:var(--buy);font-weight:700}
.toolbar label.tg{font-size:12px;color:var(--mut);display:flex;align-items:center;gap:5px;cursor:pointer}
.count{font-size:12px;color:var(--dim);margin-left:auto}
.card.hide{display:none}
.body{padding:11px 12px 13px;display:flex;flex-direction:column;gap:7px;flex:1}
.name{font-size:14px;font-weight:650;line-height:1.25}
.set{font-size:11px;color:var(--dim)}
.evrow{display:flex;align-items:baseline;gap:6px;flex-wrap:wrap}
.evrow .ev{font-size:20px;font-weight:800;color:var(--ev)}
.evrow .lbl{font-size:10px;color:var(--dim);text-transform:uppercase}
.evrow .roi{margin-left:auto;font-size:11px;font-weight:800}
.evrow .roi.up{color:var(--buy)}.evrow .roi.dn{color:var(--op)}
.prices{display:flex;gap:6px}
.pill{flex:1;background:var(--panel2);border-radius:8px;padding:5px 7px;text-align:center}
.pill .l{font-size:9px;text-transform:uppercase;color:var(--dim)}
.pill .v{font-size:13px;font-weight:700;margin-top:1px}
.pill.raw .v{color:var(--up)}.pill.psa .v{color:var(--psa)}
.tags{display:flex;flex-wrap:wrap;gap:5px}
.tag{font-size:10px;color:var(--mut);background:var(--panel2);border-radius:6px;padding:2px 6px}
.tag.ok{color:#7ee787;background:#12331d}.tag.bad{color:#ff9ca0;background:#331416}
.trend{font-size:10px;font-weight:700;border-radius:6px;padding:2px 6px}
.trend.up{color:#7ee787;background:#12331d}.trend.dn{color:#ff9ca0;background:#331416}.trend.flat{color:var(--dim);background:var(--panel2)}
.note{font-size:11px;color:var(--mut)}
/* barra resumen tipo cartera */
.stats{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0 2px}
.stat{flex:1;min-width:140px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:11px 13px}
.stat .k{font-size:11px;color:var(--dim);text-transform:uppercase;letter-spacing:.04em}
.stat .v{font-size:20px;font-weight:800;margin-top:2px}
.stat .s{font-size:11px;color:var(--mut)}
.stat.hl .v{color:var(--buy)}
.go{margin-top:auto;font-size:12px;font-weight:600;color:#58a6ff;text-decoration:none}
.go:hover{text-decoration:underline}
footer{margin-top:36px;border-top:1px solid var(--line);padding-top:14px;color:var(--dim);font-size:11px}
</style>
<div class="wrap">
<h1>Cartas para grading - ranking por EV</h1>
"""


def band_of(raw_disp):
    """Banda de precio (en moneda de presentacion): 1=<lo, 2=lo-hi, 3=>=hi."""
    if raw_disp < BANDS["lo"]:
        return 1
    if raw_disp < BANDS["hi"]:
        return 2
    return 3


def trend_html(m, trend):
    """Flecha con el cambio de EV desde el snapshot anterior (usa el historico)."""
    if not trend:
        return ''
    d = trend  # en USD; lo mostramos en moneda de presentacion
    if abs(d) < 1:
        return '<span class="trend flat">=</span>'
    arrow = '▲' if d > 0 else '▼'
    cls = 'up' if d > 0 else 'dn'
    return f'<span class="trend {cls}" title="cambio de EV desde el dia anterior">{arrow} {money(abs(d))}</span>'


def card_html(c, m, i=None, cand=False, trend=None):
    meta = game_meta(c["game"])
    g = meta["theme"]; glabel = meta["label"]
    nm = htmlmod.escape(c["name"]); st = htmlmod.escape(c.get("set", ""))
    note = htmlmod.escape(c.get("note", "")); link = htmlmod.escape(c.get("link", "#"))
    corner = ('<span class="rev">REVISAR</span>' if c.get("unverified")
              else '<span class="newf">CANDIDATA</span>' if cand
              else ('<span class="buy">COMPRAR</span>' if m["buy"] else ''))
    rank = f'<span class="rank">#{i}</span>' if i else ''
    if c.get("image_url"):
        thumb = (f'<div class="thumb"><img loading="lazy" src="{htmlmod.escape(c["image_url"])}" alt="{nm}" '
                 f'onerror="this.style.display=\'none\';this.parentNode.querySelector(\'.ph\').style.display=\'flex\'">'
                 f'<div class="ph {g}" style="display:none"><div class="g">{glabel}</div><div class="n">{nm}</div></div></div>')
    else:
        thumb = f'<div class="thumb"><div class="ph {g}"><div class="g">{glabel}</div><div class="n">{nm}</div></div></div>'
    gem = c.get("gem_rate", c.get("gem_est", 0)); fee = c.get("grading_fee", c.get("fee", 0))
    roi = m.get("roi", 0); margin = m.get("margin")
    roi_s = f'+{roi}' if roi >= 0 else f'{roi}'
    raw_d = disp(m["raw"]); band = band_of(raw_d)
    # margen de seguridad: cuanto puede bajar tu gem real antes de perder dinero
    if margin is None:
        marg_html = ''
    else:
        mcls = 'ok' if margin >= 0 else 'bad'
        marg_html = f'<span class="tag {mcls}">margen {"+" if margin>=0 else ""}{margin}pp</span>'
    return f'''<article class="card{' cand' if cand else ''}" data-game="{htmlmod.escape(c["game"])}" data-label="{glabel}" data-name="{nm.lower()}" data-ev="{disp(m["ev"])}" data-roi="{roi}" data-margin="{margin if margin is not None else -999}" data-raw="{raw_d}" data-psa10="{disp(m["psa10"])}" data-band="{band}" data-buy="{1 if m.get("buy") else 0}">
  {rank}{corner}
  {thumb}
  <div class="body">
    <div class="name">{nm}</div><div class="set">{glabel} · {st}</div>
    <div class="evrow"><span class="ev">{money(m["ev"])}</span><span class="lbl">EV neto</span><span class="roi {'up' if roi>=0 else 'dn'}">{roi_s}% ROI</span></div>
    <div class="prices">
      <div class="pill raw"><div class="l">Raw</div><div class="v">{money(m["raw"])}</div></div>
      <div class="pill psa"><div class="l">PSA 10</div><div class="v">{money(m["psa10"])}</div></div>
    </div>
    <div class="tags">{marg_html}<span class="tag">{m["mult"]}x</span><span class="tag">gem {int(gem*100)}%{' est' if cand else ''}</span>{'<span class="tag bad">PSA10 est</span>' if c.get("psa10_est") else ''}<span class="tag">fee {money(fee)}</span>{trend_html(m, trend)}</div>
    <div class="note">{note}</div>
    <a class="go" href="{link}" target="_blank">Ver ficha / foto -></a>
  </div>
</article>'''


TOOLBAR = """<div class="toolbar">
  <input id="q" type="search" placeholder="Buscar carta..." autocomplete="off">
  <div class="chips" id="games"><span class="chip on" data-g="">Todos</span>__GAMECHIPS__</div>
  <div class="chips" id="bands">
    <span class="chip on" data-b="0">Cualquier precio</span>
    <span class="chip" data-b="1">__B1__</span>
    <span class="chip" data-b="2">__B2__</span>
    <span class="chip" data-b="3">__B3__</span>
  </div>
  <label class="tg"><input type="checkbox" id="onlybuy"> solo COMPRAR</label>
  <select id="sort">
    <option value="ev">Orden: EV neto</option>
    <option value="roi">Orden: ROI %</option>
    <option value="margin">Orden: margen de seguridad</option>
    <option value="psa10">Orden: PSA 10</option>
    <option value="raw">Orden: mas barata</option>
  </select>
  <span class="count" id="count"></span>
</div>"""

FILTER_JS = """<script>
(function(){
  var q=document.getElementById('q'), sort=document.getElementById('sort'),
      onlybuy=document.getElementById('onlybuy'), grid=document.getElementById('grid'),
      count=document.getElementById('count'), games=document.getElementById('games'),
      bands=document.getElementById('bands'),
      cards=[].slice.call(grid.querySelectorAll('.card'));
  var g='', b='0';
  function num(c,k){return parseFloat(c.getAttribute('data-'+k))||0;}
  function apply(){
    var term=(q.value||'').trim().toLowerCase(), shown=0;
    cards.forEach(function(c){
      var ok=(!g||c.getAttribute('data-game')===g)
        && (b==='0'||c.getAttribute('data-band')===b)
        && (!term||c.getAttribute('data-name').indexOf(term)>=0||(c.getAttribute('data-label')||'').toLowerCase().indexOf(term)>=0)
        && (!onlybuy.checked||c.getAttribute('data-buy')==='1');
      c.classList.toggle('hide',!ok); if(ok)shown++;
    });
    var k=sort.value, dir=(k==='raw'?1:-1), vis=cards.filter(function(c){return !c.classList.contains('hide');});
    vis.sort(function(a,b){return (num(a,k)-num(b,k))*dir;});
    vis.forEach(function(c,i){c.style.order=i; var r=c.querySelector('.rank'); if(r)r.textContent='#'+(i+1);});
    count.textContent=shown+' / '+cards.length+' cartas';
  }
  function wire(box,set){ box.addEventListener('click',function(e){ var ch=e.target.closest('.chip'); if(!ch)return;
    set(ch); [].forEach.call(box.children,function(x){x.classList.toggle('on',x===ch)}); apply(); }); }
  q.addEventListener('input',apply); sort.addEventListener('change',apply); onlybuy.addEventListener('change',apply);
  wire(games,function(ch){g=ch.getAttribute('data-g');});
  wire(bands,function(ch){b=ch.getAttribute('data-b');});
  apply();
})();
</script>"""


def candidates_section(limit=8):
    """Bloque 'Novedades del scan' para la galeria principal: candidatas frescas primero."""
    try:
        with open(CAND_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return ''
    cands = data.get("candidates") or []
    if not cands:
        return ''
    # las nuevas de hoy primero, luego por EV
    cands = sorted(cands, key=lambda c: (not c.get("is_new"), -c.get("ev", 0)))[:limit]
    n_new = sum(1 for c in cands if c.get("is_new"))
    gen = htmlmod.escape(str(data.get("generated", "")))
    head = (f'<h2>🔥 Novedades del scan{f" · {n_new} nuevas hoy" if n_new else ""}</h2>'
            f'<div class="disc">Cartas recien descubiertas escaneando sets ({gen}). '
            f'gem_rate y (en tier gratis) <b>PSA10 son ESTIMADOS</b>: son pistas para investigar, '
            f'confirma el 10 real en GemRate / ventas cerradas antes de comprar. '
            f'Revisa la lista completa en <a class="go" href="candidates.html" target="_blank">candidates.html →</a></div>')
    grid = ['<div class="grid">']
    for i, c in enumerate(cands, 1):
        m = {"raw": c["raw"], "psa10": c["psa10"], "gap": c["gap"], "mult": c["mult"],
             "ev": c["ev"], "roi": c.get("roi", 0), "margin": c.get("margin"), "buy": False}
        grid.append(card_html(c, m, i=i, cand=True))
    grid.append('</div>')
    return head + "\n".join(grid)


def render(data):
    cards = sorted(data["cards"], key=lambda c: c["_m"]["ev"], reverse=True)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    buys = sum(1 for c in cards if c["_m"]["buy"])
    # chips de juego solo para los presentes en la watchlist
    present = []
    for c in cards:
        g = c["game"]
        if g not in present:
            present.append(g)
    chips = "".join(f'<span class="chip" data-g="{htmlmod.escape(g)}">{htmlmod.escape(game_meta(g)["label"])}</span>'
                    for g in present)
    # resumen tipo cartera: si compraras TODAS las marcadas COMPRAR
    buy_cards = [c for c in cards if c["_m"]["buy"]]
    capital = sum(c["_m"]["raw"] + c.get("grading_fee", 0) for c in buy_cards)
    exp_profit = sum(c["_m"]["ev"] for c in buy_cards)
    port_roi = round(exp_profit / capital * 100) if capital else 0
    parts = [HEAD]
    parts.append(f'<p class="meta">Actualizado: {now} · precios en {DISPLAY["symbol"]}'
                 + ('' if DISPLAY["fx"] == 1.0 else f' (aprox, USD×{DISPLAY["fx"]})') + '</p>')
    parts.append(
        '<div class="stats">'
        f'<div class="stat"><div class="k">Cartas</div><div class="v">{len(cards)}</div><div class="s">{buys} marcadas COMPRAR</div></div>'
        f'<div class="stat"><div class="k">Capital (comprar todas COMPRAR)</div><div class="v">{money(capital)}</div><div class="s">raw + fee</div></div>'
        f'<div class="stat hl"><div class="k">Beneficio neto esperado</div><div class="v">{money(exp_profit)}</div><div class="s">suma de EV</div></div>'
        f'<div class="stat"><div class="k">ROI de la cesta</div><div class="v">{port_roi}%</div><div class="s">beneficio / capital</div></div>'
        '</div>')
    parts.append('<div class="disc">EV = beneficio neto esperado = gem_rate*PSA10 + (1-gem_rate)*(recuperacion*raw) - raw - fee. '
                 'ROI = EV / (raw+fee). El <b>margen</b> es cuantos puntos puede bajar tu gem real antes de perder dinero (break-even). '
                 'La flecha es el cambio de EV desde ayer. Tasas de gem aproximadas: verifica variante y comp antes de comprar. No es asesoramiento financiero.</div>')
    # Novedades del scan: candidatas descubiertas hoy (arriba del todo, lo primero que ves)
    parts.append(candidates_section())
    sym = DISPLAY["symbol"]
    toolbar = (TOOLBAR.replace("__GAMECHIPS__", chips)
               .replace("__B1__", f'&lt; {sym}{BANDS["lo"]}')
               .replace("__B2__", f'{sym}{BANDS["lo"]}–{BANDS["hi"]}')
               .replace("__B3__", f'&gt; {sym}{BANDS["hi"]}'))
    parts.append('<h2>Tu watchlist</h2>')
    parts.append(toolbar)
    parts.append('<div class="grid" id="grid">')
    for i, c in enumerate(cards, 1):
        hist = c.get("history") or []
        tr = (hist[-1]["ev"] - hist[-2]["ev"]) if len(hist) >= 2 else None
        parts.append(card_html(c, c["_m"], i=i, cand=bool(c.get("pending_review")), trend=tr))
    parts.append('</div>')
    parts.append('<footer>Generado por update.py. Ajusta gem_rate en watchlist.json (GemRate/PSA pop) para afinar el EV. '
                 'Ejecuta --scan para descubrir nuevas cartas.</footer>')
    parts.append(FILTER_JS)
    parts.append('</div>')
    with open(OUTPUT_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return cards


def render_candidates(cands):
    parts = [HEAD.replace("ranking por EV", "candidatas del scan")]
    parts[0] = parts[0].replace("<h1>Cartas para grading - ranking por EV</h1>",
                                "<h1>Candidatas del scan</h1>")
    parts.append(f'<p class="meta">{len(cands)} candidatas - gem_rate ESTIMADO por rareza - revisa antes de comprar/anadir</p>')
    parts.append('<div class="disc">Descubiertas escaneando sets. El gem_rate es una estimacion por rareza, '
                 'no un dato real: confirma en GemRate/PSA pop antes de fiarte del EV.</div>')
    parts.append('<div class="grid">')
    for i, c in enumerate(cands, 1):
        m = {"raw": c["raw"], "psa10": c["psa10"], "gap": c["gap"], "mult": c["mult"],
             "ev": c["ev"], "roi": c.get("roi", 0), "margin": c.get("margin"), "buy": False}
        parts.append(card_html(c, m, i=i, cand=True))
    parts.append('</div><footer>candidates.html - generado por --scan</footer></div>')
    with open(CAND_HTML, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# ------------------------- Telegram -------------------------

def telegram(cards, new_cands=None):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat:
        return
    lines = ["<b>TGC - top del dia por EV</b>"]
    for i, c in enumerate(cards[:3], 1):
        m = c["_m"]; flag = " COMPRAR" if m["buy"] else ""
        lines.append(f"{i}. {c['name']} - EV {money(m['ev'])} ({m['roi']}% ROI, raw {money(m['raw'])}/PSA10 {money(m['psa10'])}){flag}")
    if new_cands:
        lines.append("\n<b>Nuevas candidatas del scan:</b>")
        for c in new_cands[:3]:
            lines.append(f"- {c['name']} - EV {money(c['ev'])} ({c['mult']}x)")
    text = urllib.parse.quote("\n".join(lines))
    url = (f"https://api.telegram.org/bot{token}/sendMessage?chat_id={chat}"
           f"&parse_mode=HTML&disable_web_page_preview=true&text={text}")
    try:
        http_json(url); print("  Telegram enviado.")
    except Exception as e:
        print(f"  ! Telegram fallo: {e}")


# --------------------------- main ---------------------------

def add_card(data, name, game):
    slug = norm(name)[:40]
    if any(c["id"] == slug for c in data["cards"]):
        print("Ya existe."); return data
    data["cards"].append({
        "id": slug, "name": name, "game": game, "set": "", "query": name,
        "gem_rate": 0.30, "grading_fee": 25, "seed_raw": 0, "seed_psa10": 0,
        "image_url": None,
        "link": best_link(name, name),
        "note": "Anadida a mano - completa gem_rate y precios.", "history": []})
    print(f"Anadida: {name}."); return data


def list_sets(game_key):
    """Imprime los sets de un juego (slug + nº de cartas) para rellenar config.scan.sets."""
    api_key = os.environ.get("TCG_API_KEY", "").strip()
    if not api_key:
        print("Necesitas TCG_API_KEY en el entorno."); return
    game = game_meta(game_key)["api"]
    try:
        data = http_json(f"{API_BASE}/sets?game={game}&limit=500", headers={"X-API-Key": api_key})
    except Exception as e:
        print(f"Error: {e}"); return
    rows = sorted((x for x in data.get("data", []) if x.get("count", 0) >= 40),
                  key=lambda x: -x.get("count", 0))
    print(f"{len(rows)} sets de {game} (count>=40), por nº de cartas:")
    for x in rows:
        print(f'  {x.get("count",0):>4}  {x["slug"]}   ({x.get("name","")})')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--add", metavar="NOMBRE")
    ap.add_argument("--game", default="pokemon_jp",
                    help="clave de juego (pokemon_jp, onepiece, magic, yugioh, lorcana, ...)")
    ap.add_argument("--scan", action="store_true", help="escanea scan_sets y propone candidatas")
    ap.add_argument("--autoadd", action="store_true", help="con --scan, mete las mejores en la watchlist")
    ap.add_argument("--mock", action="store_true", help="prueba el scan con datos de ejemplo")
    ap.add_argument("--list-sets", metavar="JUEGO", dest="list_sets",
                    help="lista los sets (slug + nº cartas) de un juego para el config del scan")
    args = ap.parse_args()

    if args.list_sets:
        list_sets(args.list_sets); return

    data = load()
    cfg = data.get("config", {})
    # fusiona juegos personalizados definidos en watchlist.json > config > games
    for k, v in (cfg.get("games") or {}).items():
        GAMES[k] = {**game_meta(k), **v}
    # moneda de presentacion y umbrales de banda de precio
    dcfg = cfg.get("display") or {}
    DISPLAY["symbol"] = dcfg.get("symbol", DISPLAY["symbol"])
    DISPLAY["fx"] = dcfg.get("fx", DISPLAY["fx"])
    bcfg = cfg.get("bands") or {}
    BANDS["lo"] = bcfg.get("lo", BANDS["lo"])
    BANDS["hi"] = bcfg.get("hi", BANDS["hi"])

    if args.add:
        data = add_card(data, args.add, args.game); save(data); return

    new_cands = []
    if args.scan:
        api_key = os.environ.get("TCG_API_KEY", "").strip()
        print("Escaneando sets...")
        cands, added = scan(data, api_key, args.autoadd, mock=args.mock)
        new_cands = [c for c in cands if c.get("is_new")][:5]
        data = load()  # recargar por si autoadd modifico la watchlist

    print("Refrescando watchlist...")
    data = refresh(data, args.dry_run or args.mock)
    cards = render(data)
    for c in data["cards"]:
        c.pop("_m", None)
    save(data)
    telegram(cards, new_cands)
    print(f"Listo -> {OUTPUT_HTML}")


if __name__ == "__main__":
    main()
