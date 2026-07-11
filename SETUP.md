# Puesta en marcha: que se actualice SOLO (tu no ejecutas nada)

La idea: el agente corre **en los servidores de GitHub** (gratis) una vez al dia,
regenera la galeria y la publica en una **URL que se refresca sola**. Tu solo
abres el enlace. No hace falta tener el ordenador encendido ni ejecutar nada.

## Opcion A — GitHub Pages (recomendada, cero mantenimiento)

1. **Crea un repositorio** en GitHub (ej. `tgc-grading`).
   - Sube el **contenido de esta carpeta `agente/` a la RAIZ del repo**
     (que en la raiz queden `update.py`, `watchlist.json`, `index.html` y la
     carpeta `.github/`). Puedes arrastrar los ficheros en la web de GitHub:
     "Add file" → "Upload files".

2. **Activa Pages**: repo → **Settings → Pages** → en "Build and deployment",
   Source = **GitHub Actions**. (No elijas "Deploy from a branch".)

3. **Activa los workflows**: pestaña **Actions** → si pide confirmacion,
   "I understand my workflows, enable them".

4. **(Recomendado) Clave gratis para descubrir cartas nuevas**: Settings →
   **Secrets and variables → Actions → New repository secret**:
   - `TCG_API_KEY` — clave GRATIS de https://tcgpricelookup.com (registro → pagina
     Developers → copia la key). Sin ella el scan no encuentra nada.
   - `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` — si quieres el aviso diario.

   **Qué te da la clave gratis** (plan Free): raw real, imagen y URL exacta de cada
   carta, y activa el **scan diario** que rastrea sets y te propone **cartas nuevas
   cada día** (aparecen arriba en "🔥 Novedades del scan"). El plan gratis **no trae
   PSA 10**, así que el scan lo **estima por rareza** (marcado como "PSA10 est"):
   son pistas para investigar, confirma el 10 real antes de comprar. Para PSA 10
   automático de verdad, el plan Starter (~20 $/mes) lo incluye.

5. **Lanzalo una vez a mano**: Actions → "Actualizar y publicar galeria TGC" →
   **Run workflow**. Al terminar, tu galeria vive en:
   `https://TU_USUARIO.github.io/TU_REPO/`

A partir de ahi se regenera **cada dia a las 07:00 UTC (~09:00 en Espana)** sola.
Guarda esa URL en el movil; siempre tendra los datos del dia.

> **Privacidad**: en el plan gratuito, Pages solo funciona con **repo publico**
> (la galeria seria visible por quien tenga el link; no sensible: son precios de
> cartas). Para repo **privado** con Pages necesitas plan de pago. Si prefieres
> privado y gratis, usa la Opcion B.

## Opcion B — Repo privado sin Pages

Mismo repo pero **privado**. El Action sigue corriendo y **commitea** el
`index.html` actualizado cada dia. Para verlo, abres el fichero `index.html` en
la web del repo (o lo descargas). Se actualiza solo igual; solo cambia la comodidad
de la visualizacion.

## Opcion C — 100% local (tu Mac, sin GitHub)

Si no quieres GitHub, programa un `cron`/`launchd` que ejecute el agente cuando
el Mac este encendido:
```
0 9 * * *  cd /ruta/al/agente && /usr/bin/python3 update.py --scan >> log.txt 2>&1
```
Inconveniente: solo corre si el ordenador esta encendido a esa hora.

---

**Recomendacion**: Opcion A. Es la unica en la que, de verdad, tu no tocas nada:
GitHub ejecuta, publica y tu solo miras la web.
