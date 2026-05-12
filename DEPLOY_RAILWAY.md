# Deploy Railway

Este projeto usa `railway.toml` para forcar o fluxo de deploy em producao.

## O que roda no deploy

- `preDeployCommand`: `python manage.py migrate`
- `startCommand`: `gunicorn magnusRotinas_django.wsgi --log-file -`

Assim, a migration roda antes do web subir e o deploy deve falhar cedo se o banco nao estiver consistente.

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
- Se a migration falhar no `preDeployCommand`, o deploy nao deve prosseguir para evitar erro 500 no app.
