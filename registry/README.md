# registry

## Run

```bash
cp .env.example .env
# fill required vars: POSTGRES_PASSWORD, KEYCLOAK_ADMIN_PASSWORD,
# KEYCLOAK_SECRET, REGISTRY_DEFAULT_USER_PASSWORD
docker compose up -d
```

## Endpoints

| Service   | URL                          |
|-----------|-------------------------------|
| Registry  | http://localhost:8081         |
| Keycloak  | http://localhost:8080/auth    |
| Postgres  | localhost:5432                |

Stop: `docker compose down`.
