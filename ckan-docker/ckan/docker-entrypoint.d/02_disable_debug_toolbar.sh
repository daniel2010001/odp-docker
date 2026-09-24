#!/bin/bash
# Apaga el Flask-DebugToolbar en el CKAN de desarrollo.
#
# ─── Por qué hace falta ──────────────────────────────────────────────────────
# La imagen `ckan-dev` fuerza `debug = true` en el ini en CADA arranque
# (`/srv/app/start_ckan_development.sh:11`), y `debug` activa el
# Flask-DebugToolbar (`ckan/config/middleware/flask_app.py:252`). El toolbar
# vuelca la configuración entera —incluidas `CKAN_SQLALCHEMY_URL`,
# `CKAN_DATASTORE_WRITE_URL` y `CKAN_DATASTORE_READ_URL`, con usuario y
# contraseña en claro— a cualquiera que abra `http://<host>:5000/` sin
# autenticarse. Medido el 2026-09-24: 9 credenciales expuestas en la home.
#
# ─── Por qué un script y no una variable de entorno ─────────────────────────
# `CKAN___DEBUG` (tres guiones bajos) sí llega a la config global —verificado:
# `debug` quedaba en `False`— pero NO apaga el toolbar, porque el toolbar no lee
# esa config: lee el ini, que este mismo arranque acaba de reescribir con
# `debug = true`. Las claves equivocadas son silenciosas: `CKAN__DEBUG` mapea a
# `ckan.debug` y `CKAN_DEBUG` a `ckan_debug`, una clave que no lee nadie.
#
# Los scripts de `/docker-entrypoint.d/` se ejecutan DESPUÉS de esa línea
# (`start_ckan_development.sh:42-52`) y ANTES de levantar el servidor, así que
# son el único punto de intervención correcto sin reconstruir la imagen.
#
# ─── Qué NO rompe ───────────────────────────────────────────────────────────
# Ni el hot reload ni el depurador de Werkzeug: en `ckan/cli/server.py` el
# reloader y el debugger dependen de sus propias banderas (`--disable-reloader`
# / `--disable-debugger`), no de `debug`. Se pierde el JS/CSS sin minificar de
# la UI nativa de CKAN y el modo debug de sus plantillas: irrelevante, el portal
# es la interfaz y CKAN es backend.
#
# ─── Nota ───────────────────────────────────────────────────────────────────
# Este script se ejecuta con `.` (sourced) desde el script de arranque, así que
# NO usa `set -euo pipefail`: alteraría el shell que lo invoca y podría tumbar el
# arranque de CKAN. Por la misma razón no hay `exit`.

echo "[umss] Apagando el modo debug de CKAN: el Flask-DebugToolbar publica la config entera, con credenciales de base"
if ! ckan config-tool "$CKAN_INI" -s DEFAULT "debug = false"; then
    echo "[umss] ADVERTENCIA: no se pudo apagar debug; el Flask-DebugToolbar va a exponer la config de CKAN" >&2
fi
