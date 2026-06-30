# Resumo do deploy Docker - Magnus Rotinas

Este arquivo resume o que foi criado para permitir o deploy da aplicacao Django `ApontamentosMagnus` em um servidor novo usando Docker Compose.

## 1. Estrutura geral criada

Foi criado um deploy com dois containers separados:

```text
magnus-rotinas-app-prod
magnus-rotinas-db-prod
```

Cada container tem uma responsabilidade propria:

```text
magnus-rotinas-app-prod -> aplicacao Django rodando com Gunicorn
magnus-rotinas-db-prod  -> banco PostgreSQL 16
```

O banco de dados fica persistido em um volume Docker:

```text
magnus-rotinas-db-prod-data
```

Isso significa que reiniciar, recriar ou atualizar o container da aplicacao nao apaga os dados do banco.

## 2. Arquivos criados no projeto

### `Dockerfile`

Define como montar a imagem Docker da aplicacao Django.

Ele faz, em resumo:

```text
1. Usa Python 3.13 slim
2. Instala dependencias do sistema
3. Instala dependencias do requirements.txt
4. Copia o projeto para dentro da imagem
5. Configura o entrypoint
6. Inicia o Gunicorn
```

### `.dockerignore`

Evita copiar arquivos desnecessarios para dentro da imagem Docker, como:

```text
.git
.venv
*.sqlite3
staticfiles
output
.env
.env.*
```

### `docker-compose.production.yml`

Arquivo principal da stack de producao.

Ele sobe dois servicos:

```text
postgres
app
```

O servico `postgres` usa:

```text
image: postgres:16
container_name: magnus-rotinas-db-prod
```

O servico `app` usa:

```text
build: Dockerfile
container_name: magnus-rotinas-app-prod
porta externa: 8099
porta interna: 8000
```

A aplicacao se conecta ao banco pela rede interna do Docker usando o host:

```text
postgres
```

### `docker/entrypoint.sh`

Script executado quando o container da aplicacao inicia.

Ele faz:

```text
1. Aguarda o PostgreSQL ficar disponivel
2. Roda python manage.py migrate --noinput
3. Roda python manage.py collectstatic --noinput
4. Inicia o comando final do container, que e o Gunicorn
```

Isso garante que, ao subir a stack, as migrations sejam aplicadas automaticamente.

### `.env.production.example`

Modelo das variaveis necessarias para producao.

Nao contem senhas reais.

As variaveis principais sao:

```text
APP_PORT
POSTGRES_DB
POSTGRES_USER
DB_PASSWORD
SECRET_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
FORCE_SCRIPT_NAME
STATIC_URL
```

### `DEPLOY_DOCKER.md`

Documento de referencia com informacoes gerais do deploy Docker.

### `DEPLOY_PASSO_A_PASSO.md`

Guia detalhado com o passo a passo operacional para fazer deploy no servidor.

Ele inclui:

```text
1. Commit e push na main
2. Conexao no servidor
3. git pull origin main
4. Conferencia do .env.production
5. Subida da stack Docker
6. Testes com curl
7. Configuracao do Nginx
8. Criacao do superusuario
9. Troubleshooting
```

## 3. Ajustes feitos no Django

O arquivo `magnusRotinas_django/settings.py` foi ajustado para ler configuracoes por variaveis de ambiente.

Foram externalizadas configuracoes como:

```text
SECRET_KEY
ALLOWED_HOSTS
CSRF_TRUSTED_ORIGINS
STATIC_URL
FORCE_SCRIPT_NAME
DATABASE_URL
```

Isso permite usar valores diferentes em desenvolvimento, Railway e servidor Docker sem alterar codigo.

## 4. Configuracao de subpath `/apontamentos/`

Como a aplicacao ficou publicada em:

```text
http://170.84.202.95/apontamentos/
```

Foi necessario configurar:

```env
FORCE_SCRIPT_NAME=/apontamentos
STATIC_URL=/apontamentos/static/
```

Essas variaveis fazem o Django gerar links e arquivos estaticos com o prefixo correto.

Sem isso, o CSS era carregado como:

```text
/static/css/style.css
```

Mas o correto no servidor e:

```text
/apontamentos/static/css/style.css
```

## 5. Como ficou no servidor

A pasta da aplicacao no servidor ficou:

```text
/opt/magnus-rotinas
```

O arquivo de variaveis reais fica em:

```text
/opt/magnus-rotinas/.env.production
```

Esse arquivo nao deve ser versionado nem enviado ao GitHub.

## 6. Variaveis usadas no `.env.production`

Exemplo de estrutura:

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

## 7. Como ficou o acesso

A aplicacao roda assim:

```text
Django/Gunicorn dentro do container: porta 8000
Docker publica no servidor: porta 8099
Nginx publica para o usuario: /apontamentos/
```

Fluxo:

```text
Usuario acessa:
http://170.84.202.95/apontamentos/

Nginx encaminha para:
http://localhost:8099/

Docker encaminha para:
magnus-rotinas-app-prod:8000
```

## 8. Configuracao do Nginx

Foi usada uma location do Nginx apontando para a porta 8099:

```nginx
location /apontamentos/ {
    proxy_pass http://localhost:8099/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

O arquivo esperado no servidor e:

```text
/etc/nginx/locations/apontamentos.conf
```

Depois de alterar Nginx:

```bash
sudo nginx -t
sudo systemctl reload nginx
```

## 9. Comando principal de deploy no servidor

Dentro de `/opt/magnus-rotinas`:

```bash
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --force-recreate --remove-orphans
```

Esse comando:

```text
1. Le o arquivo .env.production
2. Constroi a imagem da aplicacao
3. Sobe o PostgreSQL
4. Sobe o Django
5. Aplica migrations
6. Coleta arquivos estaticos
7. Publica a aplicacao na porta 8099
```

## 10. Fluxo simples para novos deploys

Na maquina local:

```bash
git status
git add arquivos-alterados
git commit -m "Descricao do ajuste"
git push origin main
```

No servidor:

```bash
cd /opt/magnus-rotinas
git pull origin main
docker compose --env-file .env.production -p magnus-rotinas-prod -f docker-compose.production.yml up -d --build --force-recreate --remove-orphans
```

Verificacao:

```bash
docker ps --filter "name=magnus-rotinas"
docker logs magnus-rotinas-app-prod --tail 100
curl -i http://localhost/apontamentos/login/
curl -i http://localhost/apontamentos/static/css/style.css
```

## 11. Como criar usuario administrador

Em banco novo:

```bash
docker exec -it magnus-rotinas-app-prod python manage.py createsuperuser
```

Depois acessar:

```text
http://170.84.202.95/apontamentos/login/
```

## 12. Cuidados com seguranca

Nunca commitar:

```text
.env
.env.production
senhas reais
SECRET_KEY real
DB_PASSWORD real
```

O GitGuardian alertou um placeholder parecido com senha. Foi corrigido removendo defaults sensiveis do `docker-compose.production.yml`.

Agora o Compose exige que estas variaveis existam no ambiente:

```text
DB_PASSWORD
SECRET_KEY
```

## 13. Resultado final

O projeto passou a ter deploy reproduzivel via Docker Compose, com:

```text
- Aplicacao Django isolada em container
- Banco PostgreSQL isolado em container
- Volume persistente para dados
- Variaveis de producao fora do codigo
- Nginx expondo a aplicacao em /apontamentos/
- Guia de deploy versionado no repositorio
```