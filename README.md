# Agente TGC — ranking diario de cartas por EV de gradeo

Actualiza precios/imagenes de una watchlist, calcula el **EV neto** de mandar
cada carta a gradear, regenera una galeria `index.html` ordenada de mejor a peor,
y (opcional) te avisa por Telegram con el top del dia.

Pensado para **repo privado o local**. Solo usa la libreria estandar de Python 3
(no hace falta `pip install`).

## Ficheros
- `watchlist.json` — tus cartas (semilla: 16 ya cargadas) + config. **Es la fuente de verdad**; el agente la va actualizando y acumula historico.
- `update.py` — el agente.
- `index.html` — se (re)genera en cada ejecucion. Es lo que abres para ver el ranking.
- `.github/workflows/update.yml` — cron diario (funciona en repo privado).

## Uso rapido (local)
```bash
python3 update.py --dry-run      # genera index.html con los precios semilla (sin API)
open index.html                  # o abrelo en el navegador
```

Con datos en vivo:
```bash
export TCG_API_KEY="tu_clave"
python3 update.py                # refresca precios/imagenes reales y regenera
```

Anadir una carta que encuentres (se queda guardada en la watchlist):
```bash
python3 update.py --add "Ace Manga OP16" --game onepiece
# luego edita su gem_rate y precios en watchlist.json
```

## La API de precios (imagen + PSA 10 de una sola fuente)
Por defecto apunta a **tcgpricelookup.com** (tcgfast), tier gratis 200 req/dia,
que cubre Pokemon EN, Pokemon JP y One Piece con valores graded PSA/BGS/CGC
e imagenes. Registrate, coge la API key y ponla en `TCG_API_KEY`.

Los nombres exactos de los campos pueden variar segun su doc: en `update.py`,
la funcion `fetch_live()` usa `dig()` para probar varias rutas habituales
(`prices.market_price`, `prices.graded.psa.10`, `image`, ...). Si algun campo no
cuadra, ajusta esas rutas o `API_BASE` en la cabecera del script. Mientras no haya
clave, el agente usa los precios **semilla** y sigue generando el HTML.

Alternativa (fuentes separadas): imagenes desde `apitcg.com` o `pokemontcg.io`
(gratis, hotlinkables) y precios desde la de arriba.

> No scrapees PriceCharting: salta su deteccion de bots y va contra sus terminos.

## La formula de EV
```
EV     = gem_rate*PSA10 + (1-gem_rate)*(recovery_frac*raw) - raw - fee
ROI    = EV / (raw + fee)                         -> rentabilidad sobre lo invertido
gem_be = (raw*(1-recovery_frac) + fee) / (PSA10 - recovery_frac*raw)
margen = gem_rate - gem_be                        -> tu colchon de seguridad
```
- `gem_rate`: probabilidad de sacar PSA 10 (de GemRate / PSA pop report).
- `recovery_frac`: fraccion del raw que recuperas si NO sale 10 (default 0.85; un PSA 9 moderno suele rondar el raw).
- `fee`: coste all-in de gradear esa carta.
- **ROI %**: compara cartas de distinto precio (un +$50 sobre $12 raw no es lo mismo que sobre $500).
- **margen** (en puntos porcentuales): cuanto puede bajar tu gem *real* respecto al estimado antes de que la carta pase a perder dinero. Margen negativo = pierdes en media. Es el mejor filtro de riesgo: prefiere EV alto **con** margen amplio.
- Se marca **COMPRAR** si `EV >= buy_threshold_ev` (default $30).

Ajusta `recovery_frac`, `buy_threshold_ev` y cada `gem_rate`/`grading_fee`
en `watchlist.json`. **El gem_rate es el input mas importante**: sin el, un gap
bruto grande puede enganarte (ej. Shanks sale con EV negativo pese al gap).

## Cualquier TCG
Cada carta lleva un campo `game`. Vienen listos: `pokemon_en`, `pokemon_jp`,
`onepiece`, `magic`, `yugioh`, `lorcana`, `digimon`. Para anadir otro TCG sin
tocar codigo, declaralo en `watchlist.json > config > games`:
```json
"games": { "fab": { "api": "fleshandblood", "label": "Flesh and Blood", "theme": "gn" } }
```
- `api`: el slug que espera tu API de precios para ese juego.
- `label`: nombre visible en la galeria.
- `theme`: estilo del placeholder (`pk` `op` `mtg` `ygo` `lor` `dgm` `gn`).

## La galeria interactiva
`index.html` trae una barra de control (todo client-side, el fichero sigue siendo
autonomo, sin dependencias):
- **Buscador** por nombre/juego.
- **Chips por juego** (solo aparecen los que tienes en la watchlist).
- **Bandas de precio**: `< 50` / `50–100` / `> 100` (umbrales configurables en
  `config > bands`). Filtra por lo que te puedes permitir de un vistazo.
- **Orden**: EV neto / ROI % / margen de seguridad / PSA 10 / mas barata.
- Toggle **solo COMPRAR**.
- **Barra-resumen tipo cartera** arriba: nº de cartas, capital si compras todas
  las COMPRAR, beneficio neto esperado total y ROI de la cesta.
- **Flecha de tendencia** por carta: cambio de EV desde el snapshot anterior
  (sale del historico que ya guarda la watchlist).

### Moneda
Los precios de origen estan en USD. Para verlos en tu moneda, en
`config > display` pon `symbol` y `fx` (multiplicador USD→tu moneda, aprox.):
```json
"display": { "symbol": "€", "fx": 0.92 }
```
La fuente de verdad (history) se mantiene en USD; `fx` solo afecta a la
presentacion. Para volver a dolares: `symbol "$"`, `fx 1.0`.

### recovery_frac por carta
Ademas del global, cada carta admite su propio `recovery_frac`. Util para cartas
de textura que se rayan (Manga OP, borde negro), donde recuperas menos si no sale
el 10: ponles p.ej. `"recovery_frac": 0.6` y su margen de seguridad se ajusta.

## Descubrir cartas nuevas: `--scan`
Rastrea sets enteros, calcula el gap/EV de cada carta y te propone las mejores
como candidatas (imagen incluida). Como la API de precios no da el gem_rate, el
scan lo **estima por rareza** (SAR/SIR japonesas ~0.80, One Piece MR/SEC ~0.10, etc.).

```bash
python3 update.py --scan            # escribe candidates.json + candidates.html
python3 update.py --scan --autoadd  # ademas mete en la watchlist las de EV alto
python3 update.py --scan --mock     # prueba con datos de ejemplo, sin gastar API
```

Configuras que sets escanea y los filtros en `watchlist.json > config > scan`:
- `sets`: lista de `{game, code}` a rastrear.
- `min_raw` / `max_raw`: rango de precio raw que te interesa.
- `min_mult`: multiplicador PSA10/raw minimo.
- `min_ev`: EV minimo para aparecer como candidata.
- `autoadd_ev`: con `--autoadd`, umbral de EV para meterla sola en la watchlist.
- `max_pages_per_set`: tope de paginas por set (**cada pagina = 1 peticion**; ojo al limite de 200/dia del tier gratis).

Las cartas que entran por `--autoadd` se marcan con `source: scan` y
`pending_review: true` (aparecen como **CANDIDATA** en la galeria). Revisa y
corrige su `gem_rate` real antes de fiarte del EV.

`candidates.json` y `candidates.html` se regeneran en cada `--scan`; no hace falta
versionarlos si no quieres.

## Que se actualice SOLO (tu no ejecutas nada)
El workflow de `.github/workflows/update.yml` corre en los servidores de GitHub
cada dia, regenera la galeria, commitea el historico y **la publica en una URL de
GitHub Pages que se refresca sola**. Tu solo abres el enlace.

Guia paso a paso (crear repo, activar Pages, secrets) en **[SETUP.md](SETUP.md)**.
Resumen: sube el contenido de esta carpeta a la raiz de un repo → Settings → Pages
→ Source: GitHub Actions → (opcional) mete `TCG_API_KEY` como secret → listo.

Alternativa 100% local (solo si el Mac esta encendido):
```
0 9 * * *  cd /ruta/agente && /usr/bin/python3 update.py --scan >> log.txt 2>&1
```

## Ver la galeria
`index.html` es autonomo. En local abrelo directamente. Si algun dia lo quieres
publicar, GitHub Pages lo sirve gratis (pero eso lo haria publico).

## Avisos
- Precios y gem rates son estimaciones; **verifica variante y comp** antes de comprar
  (ej. el PSA 10 de la Nami esta inflado por copias pre-errata).
- Esto es una herramienta de informacion, **no asesoramiento financiero**.
