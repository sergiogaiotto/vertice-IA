#!/usr/bin/env bash
# Vértice — gera os secrets obrigatórios para o deploy.
#
# Uso (no seu terminal local — Git Bash, macOS Terminal, Linux ou WSL):
#
#     ./scripts/gen_secrets.sh
#
# Saída: bloco KEY=VALUE pronto para colar no painel "Environment
# Variables" do Hostinger Docker Manager (ou em um arquivo .env.production
# se você usa o fluxo SSH com scripts/deploy.sh).
#
# Pré-requisito: openssl. Já vem com Git Bash, macOS e qualquer Linux.
#     git-bash:   sempre tem
#     macOS:      sempre tem
#     Linux:      apt-get install -y openssl  (caso falte)

set -euo pipefail

if ! command -v openssl >/dev/null 2>&1; then
    printf '\033[1;31mERRO:\033[0m openssl não encontrado. Instale antes de continuar.\n' >&2
    exit 1
fi

# ANSI colors — caem para texto puro se o terminal não suportar.
if [ -t 1 ]; then
    GREEN='\033[1;32m'
    CYAN='\033[1;36m'
    DIM='\033[2m'
    RESET='\033[0m'
else
    GREEN='' ; CYAN='' ; DIM='' ; RESET=''
fi

APP_SECRET_KEY="$(openssl rand -hex 32)"
POSTGRES_PASSWORD="$(openssl rand -base64 24 | tr -d '\n')"

printf "\n${GREEN}✓ Secrets gerados.${RESET}\n"
printf "${DIM}Cole o bloco abaixo no painel Environment Variables do${RESET}\n"
printf "${DIM}Hostinger Docker Manager (uma variável por linha):${RESET}\n\n"

printf "${CYAN}# ===== Secrets obrigatórios — Vértice =====${RESET}\n"
cat <<EOF
APP_SECRET_KEY=${APP_SECRET_KEY}
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
EOF

printf "\n${CYAN}# ===== Opcionais — preencha quando configurar domínio =====${RESET}\n"
cat <<EOF
# DOMAIN=vertice.seu-dominio.com.br
# ACME_EMAIL=seu@email.com
# APP_BASE_URL=https://vertice.seu-dominio.com.br:8010
# PUBLIC_HTTPS_PORT=8010
EOF

printf "\n${CYAN}# ===== Azure OpenAI (preencha quando tiver Azure provisioned) =====${RESET}\n"
cat <<EOF
# AZURE_OPENAI_API_KEY=
# AZURE_OPENAI_ENDPOINT=https://SEU-RECURSO.openai.azure.com
# AZURE_OPENAI_API_VERSION=2024-08-01-preview
# AZURE_OPENAI_DEPLOYMENT=gpt-4o
EOF

cat <<EOF

──────────────────────────────────────────────────────────────────
  ⚠️  GUARDE estas senhas em um gerenciador (1Password, Bitwarden,
     KeePass). Elas NÃO podem ser recuperadas.

  Próximos passos:
    1) Painel Hostinger → seu projeto → Environment Variables
    2) Cole APP_SECRET_KEY e POSTGRES_PASSWORD
    3) Trigger redeploy
    4) Acesse https://IP-DA-VPS:8010 (ou seu DOMAIN) e use a tela de
       /login para CRIAR o primeiro usuário ROOT — o username e senha
       que você submeter aí viram a credencial inicial. Anote-os.

  Em modo self-signed o app fica em:
    https://IP-DA-VPS:8010
  (browser avisa "certificado não confiável" — clique "avançar")
──────────────────────────────────────────────────────────────────
EOF
