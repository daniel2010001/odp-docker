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

```sh
docker compose -f docker-compose.unified.yml up --build -d
```

- Frontend: http://localhost:8080
- CKAN API (vía proxy): http://localhost:8080/api/3/action/status_show

## Servicios

| Servicio | Descripción |
|---|---|
| `frontend` | SvelteKit (adapter-node), puerto 3000 interno |
| `frontend-proxy` | nginx: `/` → frontend, `/api/` → ckan (puerto 8080 host) |
| `ckan`, `db`, `solr`, `redis`, `datapusher`, `nginx` | stack CKAN (traído vía `include` de `ckan-docker`) |

## Conectividad

nginx reverse proxy same-origin: el frontend se build-ea con `PUBLIC_CKAN_URL` vacío y usa `/api/...` relativo, que el proxy enruta a `ckan:5000` sin reescribir el path (CKAN espera `/api/3/action/<action>`), evitando CORS.

## Checks (CI)

`.github/workflows/checks.yml` corre dos jobs en cada push y pull request a `master`:

| job | qué verifica | coste |
|---|---|---|
| `shell-tests` | que los `docker-entrypoint.d/*.sh` parseen, y que pasen todos los `ckan-docker/*/tests/*.sh` | segundos, sin Docker |
| `umss-tests` | la suite de `ckanext-umss` en `ckan/ckan-dev:2.11` con solr, postgres y redis | minutos |

Los workflows que viven dentro de `ckan-docker/.github/` y de `ckan-docker/src/ckanext-umss/.github/` pertenecen a sus repos upstream: GitHub **no** los ejecuta aquí, porque sólo lee `.github/workflows/` en la raíz del repo.

El job liviano son, literalmente, estos dos loops; localmente no hace falta Docker. El `[ -e ... ] || continue` que el workflow agrega sólo evita que un glob vacío falle por accidente:

```sh
for f in ckan-docker/*/docker-entrypoint.d/*.sh; do bash -n "$f"; done
for t in ckan-docker/*/tests/*.sh; do bash "$t"; done
```
