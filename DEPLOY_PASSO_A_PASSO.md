# Deploy da aplicacao Magnus Rotinas

## Regra de branch antes de commits

Antes de criar commits, fazer push ou iniciar qualquer deploy, confirme explicitamente qual branch deve receber as alteracoes.

- Use `main` somente quando o deploy Docker/servidor for o destino combinado.
- Use `railway-pre-docker` somente quando a Railway precisar da versao anterior ao Docker.
- Se a branch nao estiver clara, pare e pergunte antes de executar `git commit`, `git push`, `railway up` ou qualquer workflow de deploy.

Este documento descreve o processo completo para publicar a aplicacao Django `ApontamentosMagnus` no servidor Docker da Senior Curitiba.

## 1. Visao geral

A aplicacao roda em uma stack Docker Compose com dois containers:

```text
magnus-rotinas-app-prod  -> Django/Gunicorn, porta interna 8000, porta externa 8099
magnus-rotinas-db-prod   -> PostgreSQL 16, banco persistido em volume Docker
```

O acesso publico passa pelo Nginx do servidor:

```text
http://170.84.202.95/apontamentos/
```

O Nginx encaminha esse caminho para:

```text
http://localhost:8099/
```

## 2. Arquivos importantes do projeto

```text
Dockerfile
.dockerignore
docker-compose.production.yml
docker/entrypoint.sh
.env.production.example
DEPLOY_DOCKER.md
DEPLOY_PASSO_A_PASSO.md
```

O arquivo real de producao no servidor e:

```text
/opt/magnus-rotinas/.env.production
```

Esse arquivo contem senhas e chaves reais. Ele nao deve ser commitado.

## 3. Antes de fazer deploy

Antes de publicar, valide localmente:

```bash
python manage.py check
```

Veja quais arquivos foram alterados:

```bash
git status
```

Nao commite arquivos locais de banco, backups ou outputs, como:

```text
*.sqlite3
*.sqlite3-journal
output/
.env
.env.production
```

## 4. Commitar na branch confirmada

Na maquina local, dentro do projeto, confirme primeiro a branch de destino com o responsavel. Nao presuma `main` ou `railway-pre-docker`.

```bash
git status
```

Adicione somente os arquivos desejados:

```bash
git add caminho/do/arquivo
```

Crie o commit:

```bash
git commit -m "Mensagem clara do ajuste"
```

Troque para a branch confirmada, se ainda nao estiver nela:

```bash
git checkout nome-da-branch-confirmada
```

Se o trabalho foi feito em outra branch, traga para a branch confirmada somente depois de autorizacao:

```bash
git merge --ff-only nome-da-branch-de-trabalho
```

Envie para o GitHub:

```bash
git push origin nome-da-branch-confirmada
```

## 5. Conectar no servidor

O servidor interno e:

```text
10.51.69.2
```

Ele so e acessivel via VPN. Primeiro conecte na VPN.

Depois, no PowerShell ou CMD, acesse por SSH:

```bash
ssh usuario_do_servidor@10.51.69.2
```

Se estiver usando PuTTY/plink, use o comando padrao documentado pelo time de infraestrutura.

## 6. Ir para a pasta da aplicacao

No servidor:

```bash
cd /opt/magnus-rotinas
```

Confira se esta na pasta certa:

```bash
pwd
ls -la
```

A pasta deve conter arquivos como:

```text
manage.py
Dockerfile
docker-compose.production.yml
docker/
requirements.txt
.env.production
```

## 7. Atualizar o codigo no servidor

Puxe a `main` atualizada:

```bash
git pull origin main
```

Se houver conflito, pare e resolva antes de continuar.

## 8. Conferir o arquivo de ambiente

O arquivo de producao deve existir em:

```bash
/opt/magnus-rotinas/.env.production
```

Para editar:

```bash
nano .env.production
```

Conteudo esperado, com senhas reais preenchidas:

```env
APP_PORT=8099

POSTGRES_DB=magnus_rotinas
POSTGRES_USER=magnus_rotinas
DB_PASSWORD=<senha-real-do-banco>

SECRET_KEY=<secret-key-real-do-django>
ALLOWED_HOSTS=170.84.202.95,10.51.69.2,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=http://170.84.202.95,http://10.51.69.2:8099,http://localhost:8099,http://127.0.0.1:8099
FORCE_SCRIPT_NAME=/apontamentos
STATIC_URL=/apontamentos/static/
```

Para salvar no `nano`:

```text
Ctrl + O
Enter
Ctrl + X
```

Nao mostre nem envie o conteudo real desse arquivo em prints ou chats.

## 9. Subir ou atualizar a stack

No servidor, dentro de `/opt/magnus-rotinas`:

```bash
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --force-recreate --remove-orphans
```

Esse comando:

```text
1. Le as variaveis de .env.production
2. Reconstroi a imagem do Django
3. Sobe o PostgreSQL se necessario
4. Roda o entrypoint do app
5. Executa migrations
6. Executa collectstatic
7. Inicia o Gunicorn
```

## 10. Verificar containers

```bash
docker ps --filter "name=magnus-rotinas"
```

Resultado esperado:

```text
magnus-rotinas-app-prod   Up   0.0.0.0:8099->8000/tcp
magnus-rotinas-db-prod    Up   5432/tcp
```

## 11. Ver logs

```bash
docker logs magnus-rotinas-app-prod --tail 100
```

Para acompanhar em tempo real:

```bash
docker logs -f magnus-rotinas-app-prod
```

## 12. Testar aplicacao localmente no servidor

Teste direto na porta do container:

```bash
curl -i http://localhost:8099/login/
```

Teste via Nginx:

```bash
curl -i http://localhost/apontamentos/login/
```

Teste o CSS:

```bash
curl -i http://localhost/apontamentos/static/css/style.css
```

Os retornos esperados sao `200 OK` ou redirecionamentos controlados (`302`).

## 13. Configuracao do Nginx

O arquivo de location deve existir em:

```text
/etc/nginx/locations/apontamentos.conf
```

Conteudo:

```nginx
location /apontamentos/ {
    proxy_pass http://localhost:8099/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

Depois de criar ou alterar:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 14. Acessar no navegador

URL principal:

```text
http://170.84.202.95/apontamentos/login/
```

Se o CSS nao carregar, confirme:

```text
FORCE_SCRIPT_NAME=/apontamentos
STATIC_URL=/apontamentos/static/
```

E recrie o container.

## 15. Criar usuario administrador

Em banco novo, crie um superusuario:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py createsuperuser
```

Depois use esse usuario em:

```text
http://170.84.202.95/apontamentos/login/
```

## 16. Comandos uteis de manutencao

Reiniciar apenas o app:

```bash
docker restart magnus-rotinas-app-prod
```

Executar migrations manualmente:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py migrate
```

Coletar estaticos manualmente:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py collectstatic --noinput
```

Abrir shell Django:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py shell
```

Ver variaveis nao secretas dentro do container:

```bash
docker exec magnus-rotinas-app-prod env | grep -E 'APP_PORT|ALLOWED_HOSTS|CSRF_TRUSTED_ORIGINS|FORCE_SCRIPT_NAME|STATIC_URL'
```

## 17. Problemas comuns

### Erro `no such file or directory` no Docker Compose

Provavelmente voce nao esta na pasta correta ou o servidor ainda nao recebeu os arquivos Docker.

Confira:

```bash
cd /opt/magnus-rotinas
ls -la
```

Deve existir:

```text
Dockerfile
docker-compose.production.yml
docker/
```

### Erro `400 Bad Request`

Normalmente e `ALLOWED_HOSTS`.

Confira:

```bash
docker logs magnus-rotinas-app-prod --tail 100
```

E veja as variaveis:

```bash
docker exec magnus-rotinas-app-prod env | grep ALLOWED_HOSTS
```

Para os testes locais funcionarem, precisa conter:

```text
localhost,127.0.0.1
```

### App abre sem CSS

Confirme no `.env.production`:

```env
FORCE_SCRIPT_NAME=/apontamentos
STATIC_URL=/apontamentos/static/
```

Depois recrie:

```bash
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --force-recreate --remove-orphans
```

### Erro 404 em `/apontamentos/`

Confira o Nginx:

```bash
cat /etc/nginx/locations/apontamentos.conf
sudo nginx -t
sudo systemctl reload nginx
```

Teste:

```bash
curl -i http://localhost/apontamentos/login/
```

## 18. Observacoes de seguranca

Nunca commite arquivos com segredos reais:

```text
.env
.env.production
```

Senhas reais devem ficar apenas:

```text
1. No .env.production do servidor
2. Em GitHub Secrets, se o deploy automatico for usado
```

Se o GitHub ou GitGuardian alertar sobre segredo, verifique se e uma senha real ou apenas placeholder. Mesmo placeholders devem ser evitados em formato parecido com senha.

## 19. Resumo rapido do deploy manual

```bash
cd /opt/magnus-rotinas
git pull origin main
nano .env.production
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --force-recreate --remove-orphans
docker ps --filter "name=magnus-rotinas"
docker logs magnus-rotinas-app-prod --tail 100
curl -i http://localhost/apontamentos/login/
```