# odp-docker — Orquestación unificada (CKAN + Frontend)

Compose unificado que levanta el stack completo de la Plataforma de Datos Abiertos UMSS: **CKAN** (backend) + **frontend SvelteKit**, detrás de un único reverse proxy nginx (same-origin, sin CORS).

## Layout esperado

Este repo asume directorios hermanos:

```
~/projects/
├── odp/                  # frontend SvelteKit (github.com/daniel2010001/odp)
│   └── Dockerfile
└── odp-docker/           # este repo
    ├── ckan-docker/      # backend CKAN, versionado aquí (upstream + ckanext-umss)
    ├── docker-compose.unified.yml
    └── frontend-proxy/
        └── nginx.conf
```

`ckan-docker/` está **rastreado en este mismo repo**: es el upstream de CKAN congelado más el plugin `ckanext-umss`, incorporado en el commit `1a06737` para que el stack se reproduzca con un solo `git clone`. No es un repo git aparte ni está en `.gitignore`.

Para reproducir el layout:

```sh
git clone https://github.com/daniel2010001/odp.git ../odp
git clone https://github.com/daniel2010001/odp-docker.git .
```

## Uso

Antes del primer `up`, crear `.env` en la **raíz** de este repo a partir de `.env.example` y completar `SESSION_SECRET` (la clave que sella la cookie de sesión del portal; por ejemplo `openssl rand -base64 32`). Sin ella Compose se niega a arrancar. Va en el `.env` raíz y **no** en `ckan-docker/.env`: Compose interpola el compose raíz desde el `.env` de su propia carpeta, y el de `ckan-docker/` sólo alimenta el stack CKAN incluido. Rotarla invalida todas las sesiones abiertas.

Ojo con los nombres: una variable del `.env` raíz **también pisa** a la del mismo nombre en `ckan-docker/.env` (medido con docker compose 5.6.0), así que el `.env` raíz no debe repetir nombres de ese archivo.

```sh
cp .env.example .env    # y completar SESSION_SECRET
docker compose -f docker-compose.unified.yml up --build -d
```

- Frontend: http://localhost:8080
- CKAN API (vía proxy): http://localhost:8080/api/3/action/status_show

## Demo en hs-node

La demo v0 corre el stack de **producción** en hs-node (la laptop del homelab), detrás de su Caddy y **en lugar de** odp-dev: en la RAM entra un solo stack CKAN. El override `docker-compose.hs.yml` hace cuatro cosas:

- Usa el proyecto `odp-demo`, con volúmenes propios, así que los de odp-dev no se tocan.
- No publica puertos en el host y no levanta el nginx con TLS de CKAN.
- Sirve los nombres `https://demo.odp.hs.lan` (portal) y `https://api.demo.odp.hs.lan` (CKAN, que es su `ckan.site_url`).
- Fija `ORIGIN` en el frontend: detrás de Caddy, el proxy reenvía `http` y, sin ese valor, SvelteKit rechaza el login.

1. **Secretos.** `bin/new-demo-env` escribe `.env.demo` a partir de `demo.env.example`, con modo 600:
   - genera todos los secretos nuevos, nunca con valores de la plantilla ni de dev;
   - arma las URLs de la base con las mismas contraseñas con que `db` crea los roles;
   - no imprime ningún valor;
   - no sobrescribe un archivo que ya existe, porque los roles de un volumen `odp-demo` existente conservan las contraseñas viejas.

   `ckan-docker/.env` lo comparte odp-dev: una clave que falte en `.env.demo` pasa el valor de dev. Por eso el ejemplo lista todas las claves y `tests/test-hs-demo-compose.sh` lo exige. La contraseña de `ckan_admin` se lee con `grep '^CKAN_SYSADMIN_PASSWORD=' .env.demo`.
2. **Nombres.** En casa, cada nombre necesita un rewrite en AdGuard (→ `192.168.1.201`). Fuera de casa no hay AdGuard: agregá a `/etc/hosts` la línea `127.0.0.1 demo.odp.hs.lan api.demo.odp.hs.lan`, porque Caddy escucha en `0.0.0.0:443`. El certificado lo emite la CA interna de Caddy (`tls internal`), así que el navegador tiene que confiar en esa raíz o vas a tener que aceptar el aviso.
3. **Levantar.** Sin `--env-file .env.demo`, Compose se niega a arrancar porque falta `DEMO_HOST`.

   ```sh
   docker compose -f docker-compose.dev.unified.yml stop
   docker compose -f docker-compose.unified.yml -f docker-compose.hs.yml --env-file .env.demo up -d --build
   ```

4. **Volver a dev.** `stop` no borra nada, y un stack detenido sigue detenido después de reiniciar (`restart: unless-stopped`).

   ```sh
   docker compose -f docker-compose.unified.yml -f docker-compose.hs.yml --env-file .env.demo stop
   docker compose -f docker-compose.dev.unified.yml start
   ```

En el primer arranque, sobre este stack se corren los chequeos del issue #25.

## Servicios

| Servicio | Descripción |
|---|---|
| `frontend` | SvelteKit (adapter-node), puerto 3000 interno |
| `frontend-proxy` | nginx: `/` → frontend, `/api/` → ckan (puerto 8080 host) |
| `ckan`, `ckan-worker`, `db`, `solr`, `redis`, `nginx` | stack CKAN (traído vía `include` de `ckan-docker`) |

## Carga al DataStore (xloader)

Los CSV y Excel subidos como recurso se cargan al DataStore con **ckanext-xloader 2.6.0** (reemplazó a DataPusher 0.0.21, que guardaba fechas dd/mm como mm/dd sin avisar). Tres piezas:

- **`ckan-worker`** (`ckan-worker-dev` en dev): corre `ckan jobs worker` con la misma imagen que `ckan`. Sin él los jobs se acumulan en Redis y nada se carga. El `.env` de `ckan-docker/` necesita `CKAN___API_TOKEN__JWT__DECODE__SECRET`: el worker decodifica el token que mintió el contenedor web, y sin ese secreto no arranca.
- **Fechas dd/mm**: la base del DataStore usa `DateStyle 'ISO, DMY'` (lo fija `20_create_datastore.sh` en volúmenes nuevos y `04_datastore_datestyle.sh` en cada arranque de `ckan` en los existentes), y xloader corre con `parse_dates_dayfirst`. Así `03/04/2024` es el 3 de abril por las dos vías de carga de xloader. Una fecha ISO (`2024-04-03`) no cambia.
- **Tipos**: xloader carga todas las columnas como texto. Los tipos se declaran en el Diccionario de Datos del recurso y se recarga. Una columna con zona horaria (`10:30:00-04:00`) va como `timestamptz`. Como `timestamp` se pierde el offset.

Pendiente, sin resolver: si un cambio de tipo falla, la tabla puede quedar a medias o borrarse (medido en `odd/tasks/loader-eval.md`).

**Seguridad: el token de servicio de xloader.** `01_setup_xloader.sh` lo mintea para el sysadmin (365 días), y xloader 2.6.0 lo guarda en cada fila de su tabla `jobs`: `db.delete_api_key` existe, pero nadie la llama. Sin la cadena de `ckanext-umss` (`logic/action/xloader.py`), `xloader_status` lo devuelve en `task_info.api_key` a cualquiera con `resource_update` sobre el recurso, es decir, a cualquier editor del dataset. La cadena saca esa clave de la respuesta y no cambia nada más. La clave sigue **guardada en texto plano** en la tabla `jobs` de la base de CKAN y en sus backups: quien lea la base o un backup obtiene un token sysadmin, y por eso los tokens viejos se revocan (punto 2).

A verificar en producción en el primer arranque, y otra vez después de cada actualización de xloader o de CKAN:

1. Llamar a `xloader_status` sobre un recurso ya cargado, con un token propio. La respuesta no debe tener `api_key` en `task_info` ni ningún string con forma de JWT. Si aparece, la cadena no está activa, y eso no da error ni queda en el log. Revisar que el plugin se llame `xloader`, que `umss` esté en `CKAN__PLUGINS` y, por las dudas, que vaya después de `xloader`. Leyendo el código de CKAN, el orden no debería importar, pero no está medido.
2. Contar los tokens del sysadmin con `ckan user token list <sysadmin>`. Cada arranque mintea un token `xloader` nuevo y no revoca el anterior, así que se acumulan tokens sysadmin válidos por un año. Revocar los viejos. Si existen tokens `datapusher` de antes del cambio, revocarlos todos.
3. Si una versión nueva de xloader ya borra la clave por sí misma (`delete_api_key` llamada), la cadena sobra, pero no rompe nada.

**Stack dev existente:** reconstruir la imagen no alcanza. Los volúmenes `home_dir` (`/srv/app`), `site_packages_py314` y `local_bin` de `docker-compose.dev.yml` tapan lo que pip instala en la imagen, así que xloader no aparece hasta recrearlos. Los datos (`pg_data`, `ckan_storage`, `solr_data`) no se tocan:

```sh
docker compose -f docker-compose.dev.unified.yml down
docker volume rm odp-dev_home_dir odp-dev_site_packages_py314 odp-dev_local_bin
docker compose -f docker-compose.dev.unified.yml up -d --build
```

El `.env` local de `ckan-docker/` también cambia: en `CKAN__PLUGINS`, `datapusher` se reemplaza por `xloader`, y las variables `DATAPUSHER_*` y `CKAN__DATAPUSHER__*` se borran (ver `ckan-docker/.env.example`).

## Conectividad

nginx reverse proxy same-origin: el frontend se build-ea con `PUBLIC_CKAN_URL` vacío y usa `/api/...` relativo, que el proxy enruta a `ckan:5000` sin reescribir el path (CKAN espera `/api/3/action/<action>`), evitando CORS.

## Checks (CI)

`.github/workflows/checks.yml` corre dos jobs en cada push y pull request a `master`:

| job | qué verifica | coste |
|---|---|---|
| `shell-tests` | que los `docker-entrypoint.d/*.sh` parseen, y que pasen todos los `ckan-docker/*/tests/*.sh` y `tests/*.sh` | segundos, sin daemon de Docker (`test-hs-demo-compose.sh` usa el CLI de Compose y `jq`) |
| `umss-tests` | la suite de `ckanext-umss` en `ckan/ckan-dev:2.12` con solr, postgres y redis | minutos |

Los workflows que viven dentro de `ckan-docker/.github/` y de `ckan-docker/src/ckanext-umss/.github/` pertenecen a sus repos upstream: GitHub **no** los ejecuta aquí, porque sólo lee `.github/workflows/` en la raíz del repo.

El job liviano son, literalmente, estos dos loops; localmente no hace falta el daemon de Docker. El `[ -e ... ] || continue` que el workflow agrega sólo evita que un glob vacío falle por accidente:

```sh
for f in ckan-docker/*/docker-entrypoint.d/*.sh; do bash -n "$f"; done
for t in ckan-docker/*/tests/*.sh tests/*.sh; do bash "$t"; done
```
