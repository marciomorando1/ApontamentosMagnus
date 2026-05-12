# Deploy Railway

Este projeto usa o `Procfile` como protecao principal e o `railway.toml` apenas para reforcar a migration antes do deploy.

## O que roda no deploy

- `preDeployCommand`: `python manage.py migrate`
- `release`: `python manage.py migrate`
- `web`: `python manage.py migrate && python manage.py collectstatic --noinput && gunicorn magnusRotinas_django.wsgi --log-file -`

Assim, a migration roda antes do deploy e, no boot do web, o projeto ainda garante novamente `migrate` e `collectstatic` antes de subir o `gunicorn`.

## Fluxo recomendado

1. Rodar os testes locais:
   `python manage.py test horas`
2. Subir a branch para o GitHub e abrir PR.
3. Fazer o deploy:
   `railway up`
4. Conferir logs:
   `railway logs`

## Verificacao das migrations

Se precisar validar o banco de producao a partir da maquina local, use a URL externa do PostgreSQL com SSL.

Exemplo:

```powershell
$env:DATABASE_URL='postgresql://...@metro.proxy.rlwy.net:PORT/railway?sslmode=require'
python manage.py showmigrations horas
```

## Observacoes

- O host interno `postgres.railway.internal` so resolve dentro da rede privada da Railway.
- Se `migrate` ou `collectstatic` falharem no boot, o processo web nao sobe, evitando publicar uma versao sem banco ou sem CSS.
