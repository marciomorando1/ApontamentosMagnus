# Deploy Docker em producao

Este projeto esta preparado para deploy via GitHub Actions acessando o servidor por SSH. A stack sobe com Docker Compose, incluindo container do Django, container do PostgreSQL e volume persistente do banco.

## Arquivos principais

- `Dockerfile`: imagem do Django com Python, dependencias e Gunicorn.
- `docker/entrypoint.sh`: aguarda o PostgreSQL, roda migrations, coleta arquivos estaticos e inicia o comando do container.
- `docker-compose.production.yml`: stack de producao com `postgres` e `app`.
- `.github/workflows/deploy-main.yml`: workflow que copia o projeto para o servidor via SSH e roda o Docker Compose.
- `.env.production.example`: exemplo das variaveis da aplicacao.

## GitHub Environment `production`

Configure como Variables:

```text
SSH_HOST=10.51.69.2
SSH_USER=usuario_do_servidor
SSH_PORT=22
DEPLOY_PATH=/opt/magnus-rotinas
APP_PORT=8094
POSTGRES_DB=magnus_rotinas
POSTGRES_USER=magnus_rotinas
ALLOWED_HOSTS=10.51.69.2
CSRF_TRUSTED_ORIGINS=http://10.51.69.2:8094
```

Configure como Secrets:

```text
SSH_PRIVATE_KEY=chave_privada_ssh_com_acesso_ao_servidor
DB_PASSWORD=senha-forte-do-postgres
SECRET_KEY=chave-secreta-forte-do-django
```

## Chave SSH

A chave publica correspondente a `SSH_PRIVATE_KEY` precisa estar no arquivo `~/.ssh/authorized_keys` do usuario configurado em `SSH_USER` no servidor.

## Comandos executados no servidor

O workflow executa, dentro de `DEPLOY_PATH`:

```bash
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml build --pull
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml down --remove-orphans
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --remove-orphans
```

## Deploy manual

Se precisar rodar manualmente no servidor:

```bash
cd /opt/magnus-rotinas
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --remove-orphans
```

## Primeiro acesso

Depois da primeira subida, crie o usuario administrador:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py createsuperuser
```

## Observacoes

- O banco fica persistido no volume `magnus-rotinas-db-prod-data`.
- O app escuta internamente na porta `8000`.
- A porta externa padrao e `8094`, ajustavel por `APP_PORT`.
- O proxy do servidor deve encaminhar o dominio ou IP para a porta externa configurada.