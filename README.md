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
| `shell-tests` | que los `docker-entrypoint.d/*.sh` parseen, y que pasen todos los `ckan-docker/*/tests/*.sh` y `tests/*.sh` | segundos, sin Docker |
| `umss-tests` | la suite de `ckanext-umss` en `ckan/ckan-dev:2.12` con solr, postgres y redis | minutos |

Los workflows que viven dentro de `ckan-docker/.github/` y de `ckan-docker/src/ckanext-umss/.github/` pertenecen a sus repos upstream: GitHub **no** los ejecuta aquí, porque sólo lee `.github/workflows/` en la raíz del repo.

El job liviano son, literalmente, estos dos loops; localmente no hace falta Docker. El `[ -e ... ] || continue` que el workflow agrega sólo evita que un glob vacío falle por accidente:

```sh
for f in ckan-docker/*/docker-entrypoint.d/*.sh; do bash -n "$f"; done
for t in ckan-docker/*/tests/*.sh tests/*.sh; do bash "$t"; done
```
