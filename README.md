# odp-docker — Orquestación unificada (CKAN + Frontend)

Compose unificado que levanta el stack completo de la Plataforma de Datos Abiertos UMSS: **CKAN** (backend) + **frontend SvelteKit**, detrás de un único reverse proxy nginx (same-origin, sin CORS).

## Layout esperado

Este repo asume directorios hermanos:

```
~/projects/
├── odp/                  # frontend SvelteKit (github.com/daniel2010001/odp)
│   └── Dockerfile
└── odp-docker/           # este repo
    ├── ckan-docker/      # clon de github.com/ckan/ckan-docker (+ ckanext-umss)
    ├── docker-compose.unified.yml
    └── frontend-proxy/
        └── nginx.conf
```

`ckan-docker/` es un repo git separado (clon del upstream de CKAN con los cambios locales del plugin `ckanext-umss`), por eso está en `.gitignore`.

Para reproducir el layout:

```sh
git clone https://github.com/daniel2010001/odp.git ../odp
git clone https://github.com/ckan/ckan-docker.git ckan-docker   # + tu fork con ckanext-umss
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
