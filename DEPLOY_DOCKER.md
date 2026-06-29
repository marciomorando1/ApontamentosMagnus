# Deploy Docker em producao

Este projeto esta preparado para deploy com Docker Compose no servidor. A stack inclui container do Django, container do PostgreSQL e volume persistente do banco.

## Arquivos principais

- `Dockerfile`: imagem do Django com Python, dependencias e Gunicorn.
- `docker/entrypoint.sh`: aguarda o PostgreSQL, roda migrations, coleta arquivos estaticos e inicia o comando do container.
- `docker-compose.production.yml`: stack de producao com `postgres` e `app`.
- `.github/workflows/deploy-main.yml`: workflow para deploy por GitHub Actions em runner self-hosted na VM.
- `.env.production.example`: exemplo das variaveis da aplicacao.

## GitHub Environment `production`

Configure como Variables:

```text
APP_PORT=8099
POSTGRES_DB=magnus_rotinas
POSTGRES_USER=magnus_rotinas
ALLOWED_HOSTS=170.84.202.95,10.51.69.2,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://170.84.202.95,http://10.51.69.2:8099,http://localhost:8099,http://127.0.0.1:8099
FORCE_SCRIPT_NAME=/apontamentos
STATIC_URL=/apontamentos/static/
```

Configure como Secrets:

```text
DB_PASSWORD=<definir-no-github-ou-no-env-do-servidor>
SECRET_KEY=<definir-no-github-ou-no-env-do-servidor>
```

## Deploy manual

No servidor:

```bash
cd /opt/magnus-rotinas
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --remove-orphans
```

## Primeiro acesso

Depois da primeira subida, crie o usuario administrador:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py createsuperuser
```

## Nginx

Para publicar em `/apontamentos/`, crie `/etc/nginx/locations/apontamentos.conf`:

```nginx
location /apontamentos/ {
    proxy_pass http://localhost:8099/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Depois rode:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## Observacoes

- O banco fica persistido no volume `magnus-rotinas-db-prod-data`.
- O app escuta internamente na porta `8000`.
- A porta externa padrao e `8099`, ajustavel por `APP_PORT`.
- `FORCE_SCRIPT_NAME` e `STATIC_URL` mantem links e assets funcionando no subpath `/apontamentos/`.