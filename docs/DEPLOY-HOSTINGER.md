# Deploy do Vértice em VPS Hostinger

Guia completo do zero ao primeiro login, em ~15 minutos.

A stack publicada:

```
                              Internet (HTTPS)
                                    │
                                    ▼
                         ┌─────────────────────┐
                         │   Caddy (80/443)    │  TLS automático Let's Encrypt
                         │   reverse proxy     │  HTTP/2 + HTTP/3
                         └──────────┬──────────┘
                                    │ rede interna
            ┌───────────────────────┼───────────────────────┐
            ▼                       ▼                       ▼
    ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
    │  vertice     │◀──────▶│  postgres    │◀──────▶│  pgbackup    │
    │  FastAPI/    │  pool  │  16-alpine   │ pg_dump│  diário      │
    │  uvicorn     │ asyncpg│              │        │  (volume)    │
    └──────────────┘        └──────────────┘        └──────────────┘
```

Apenas as portas **80** e **443** são expostas publicamente; Postgres fica
acessível só via rede Docker interna.

---

## 1) Provisionar a VPS

### 1.1 Criar o servidor

No painel da Hostinger:

- **Sistema operacional**: Ubuntu 22.04 LTS ou 24.04 LTS
- **Plano sugerido**: KVM 2 (2 vCPU / 8 GB RAM) — confortável. KVM 1 (1
  vCPU / 4 GB) funciona com `VERTICE_MEM_LIMIT=512m`.
- **SSH key**: cadastre sua chave pública na criação. Evita senha de root.

Após criar, anote o **IP público IPv4**.

### 1.2 Apontar o domínio

No seu DNS (Hostinger ou outro):

```
Tipo   Nome   Valor                TTL
A      @      <IP-da-VPS>          300
A      www    <IP-da-VPS>          300   (opcional)
```

Aguarde a propagação (use `dig +short SEU_DOMINIO` para checar).

### 1.3 Provisionar Docker, firewall e usuário

SSH como `root` e rode o instalador:

```bash
ssh root@SEU_IP

curl -fsSL https://raw.githubusercontent.com/SEU_USER/SEU_REPO/main/scripts/install_docker.sh \
     -o install_docker.sh
chmod +x install_docker.sh
./install_docker.sh
```

O script é **idempotente** — pode rodar de novo sem problema. Ele:

- instala Docker Engine + plugin compose (repo oficial)
- cria usuário `deploy` com sudo NOPASSWD e no grupo `docker`
- copia sua `authorized_keys` para o `deploy`
- habilita UFW (portas 22, 80, 443) e fail2ban
- cria 2 GB de swap se a RAM for menor que 4 GB
- liga unattended-upgrades para patches de segurança

> 💡 Se sua VPS já tem Docker, o script detecta e pula a instalação.

---

## 2) Clonar o repositório e configurar o ambiente

```bash
# Saia do shell de root e use o usuário deploy:
exit
ssh deploy@SEU_IP

git clone https://github.com/SEU_USER/SEU_REPO.git vertice
cd vertice
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

**Trocar OBRIGATORIAMENTE em `.env.production`:**

| Variável                    | Como gerar                                          |
| --------------------------- | --------------------------------------------------- |
| `DOMAIN`                    | seu domínio (ex: `vertice.exemplo.com.br`)         |
| `ACME_EMAIL`                | seu e-mail (Let's Encrypt usa para avisos)         |
| `APP_SECRET_KEY`            | `openssl rand -hex 32`                              |
| `ADMIN_BOOTSTRAP_PASSWORD`  | senha forte do admin (será trocada após 1º login)  |
| `POSTGRES_PASSWORD`         | `openssl rand -base64 24`                           |

Opcional: chaves de LLM (OpenAI / Maritaca / GAIA), LangFuse, MLflow, OPA.
Sem chaves, o Vértice roda em **modo mock** — todas as telas ficam
navegáveis para validação.

---

## 3) Subir a stack

```bash
./scripts/deploy.sh
```

O script valida o `.env.production`, faz `git pull`, builda a imagem,
sobe Caddy + app + Postgres + pgbackup com `docker compose`, e
**aguarda o healthcheck do app ficar verde** antes de imprimir o sumário.

Tempo total: ~3 min na primeira vez (build), ~30s nas próximas.

Caddy emite o certificado Let's Encrypt no primeiro acesso a
`https://SEU_DOMINIO`. Pode levar até 1 minuto na primeira requisição.

---

## 4) Primeiro login

Acesse `https://SEU_DOMINIO` e entre com:

- usuário: `admin`
- senha: o `ADMIN_BOOTSTRAP_PASSWORD` que você definiu

**Troque a senha imediatamente** em `Usuários → admin → Alterar senha`.

---

## 5) Operação diária

### Logs ao vivo

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f vertice
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f caddy
```

Logs com rotação (10 MB × 5 arquivos por serviço, configurado no compose).

### Atualizar a aplicação

```bash
cd ~/vertice
./scripts/deploy.sh
```

O script faz `git pull`, builda só se mudar e faz `up -d` zero-downtime
(Caddy mantém conexões drenando enquanto o app sobe a versão nova).

### Status

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

### Console SQL

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
     exec postgres psql -U vertice vertice
```

### Parar tudo (mantém dados)

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production down
```

### Parar e apagar volumes (⚠️ destrutivo — perde DB)

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production down -v
```

---

## 6) Backups

O serviço `pgbackup` faz `pg_dump` diário em **03:00 UTC** (configurável
em `BACKUP_AT_HOUR_UTC`) com retenção de 7 dias (`BACKUP_KEEP_DAYS`).

Os dumps ficam no volume nomeado `vertice_backups`, mapeado para
`/var/backups/postgres` dentro do container.

### Listar backups disponíveis

```bash
./scripts/restore.sh --list
```

### Copiar um dump para fora do servidor

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
     cp pgbackup:/var/backups/postgres/vertice_<timestamp>.dump ./

# do laptop, baixar via scp:
scp deploy@SEU_IP:~/vertice/vertice_<timestamp>.dump ./
```

### Restaurar de um dump (⚠️ destrutivo)

```bash
./scripts/restore.sh /var/backups/postgres/vertice_<timestamp>.dump
```

Pede confirmação interativa. Para de derrubar o app, dropa e recria o
banco, roda `pg_restore --jobs=4`, sobe app de volta.

---

## 7) TLS / Certificados

Caddy gerencia certificados Let's Encrypt automaticamente:

- Emissão automática no primeiro request HTTPS.
- Renovação automática a cada ~60 dias.
- Estado persistido em volume `vertice_caddy_data` (perdas zero entre
  redeploy).

### Forçar renovação

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
     restart caddy
```

### Ver os certificados emitidos

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production \
     exec caddy ls /data/caddy/certificates/acme-v02.api.letsencrypt.org-directory/
```

---

## 8) Tuning para o plano da VPS

Edite `.env.production` conforme RAM disponível:

| Plano (RAM) | `PG_SHARED_BUFFERS` | `PG_EFFECTIVE_CACHE_SIZE` | `VERTICE_MEM_LIMIT` |
| ----------- | ------------------- | ------------------------- | ------------------- |
| 1 GB        | `128MB`             | `512MB`                   | `512m`              |
| 2 GB        | `256MB`             | `1GB`                     | `768m`              |
| 4 GB        | `512MB`             | `2GB`                     | `1g`                |
| 8 GB        | `1GB`               | `4GB`                     | `2g`                |
| 16 GB       | `2GB`               | `8GB`                     | `3g`                |

Após editar, aplique:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

---

## 9) Troubleshooting

### Caddy fica em loop tentando emitir certificado

- Confira se o **DNS A** aponta corretamente: `dig +short SEU_DOMINIO`
- Confira se as portas **80 e 443 estão abertas** no firewall da VPS
  (UFW + qualquer firewall externo da Hostinger): `ufw status`
- Confira logs do Caddy: `docker compose ... logs -f caddy`
- Erros comuns:
  - `no such host` → DNS errado
  - `connection refused` → outro processo (Apache/nginx legado) ocupando 80/443
  - `rate limit` → você pediu muitos certs em uma hora; aguarde 1h.

### App fica `unhealthy`

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs vertice
```

Causas comuns:

- `password authentication failed`: confira `POSTGRES_PASSWORD` no env.
- `database "vertice" does not exist`: o Postgres não terminou o init no
  primeiro boot. `docker compose ... restart vertice`.
- OOM (out of memory): aumente `VERTICE_MEM_LIMIT` ou reduza
  `PG_SHARED_BUFFERS`.

### Disco cheio

```bash
docker system df
docker system prune -af --volumes   # ⚠️ remove volumes NÃO usados (cuidado)
```

Os volumes nomeados (`vertice_pgdata`, `vertice_backups`,
`vertice_caddy_data`, `vertice_caddy_config`) **não** são removidos por
`prune` enquanto a stack estiver rodando.

### "permission denied" no docker

Você esqueceu de re-logar após `usermod -aG docker`. Solução rápida:

```bash
newgrp docker
# ou
exit && ssh deploy@SEU_IP
```

---

## 10) Segurança operacional

A stack já vem com:

- ✅ Postgres **sem porta exposta** publicamente
- ✅ App rodando como **usuário não-root** (uid 10001)
- ✅ Caddy com **HSTS, X-Frame-Options, CSP-friendly headers**
- ✅ Secrets via env vars (não em código)
- ✅ Limite de upload em 50 MB no Caddy
- ✅ Healthchecks ativos em Caddy + app + Postgres
- ✅ UFW + fail2ban no host
- ✅ Logs com rotação (10 MB × 5)

Recomendações adicionais:

- **Trocar a senha SSH ou desabilitar password login** após confirmar
  que sua chave funciona:
  ```bash
  sudo sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
  sudo systemctl reload ssh
  ```
- **Backups offsite**: o `pgbackup` guarda no volume Docker da própria
  VPS — se a VPS morrer, perde tudo. Configure um cron extra que copie
  os dumps para S3/Backblaze/rclone.
- **Monitoramento**: ative o LangFuse ou OTel via env vars; para infra,
  considere instalar `node_exporter` + Prometheus externo.

---

## Apêndice — Comandos Compose comuns

Sempre passar `-f docker-compose.prod.yml --env-file .env.production`:

```bash
DC="docker compose -f docker-compose.prod.yml --env-file .env.production"

$DC ps                                 # status dos serviços
$DC logs -f vertice                    # logs do app ao vivo
$DC restart vertice                    # restart só do app
$DC exec vertice bash                  # shell dentro do container
$DC exec postgres psql -U vertice      # console SQL
$DC down                               # para tudo (mantém dados)
$DC down -v                            # para tudo e APAGA volumes
$DC pull                               # atualiza imagens (se usa registry)
$DC up -d --build                      # rebuild local + sobe
```

Crie um alias no `~/.bashrc` do `deploy` para encurtar:

```bash
echo 'alias dc="docker compose -f ~/vertice/docker-compose.prod.yml --env-file ~/vertice/.env.production"' \
     >> ~/.bashrc
source ~/.bashrc
```
