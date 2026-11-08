#!/bin/bash
# Configuração do Agente 1 na VPS: senha da página, WhatsApp (QR code) e login do Claude.
# Rode como root:  bash /opt/agente-resumo/vps/configurar.sh
set -uo pipefail
W=/opt/agente-resumo/bin/wacli
ENVF=/etc/agente-resumo.env

passo() { echo; echo "=================================================="; echo " $1"; echo "=================================================="; }

passo "1 de 3  SENHA DA PAGINA resumo.andrelink.tech"
if grep -q '^SENHA_HASH=pbkdf2' "$ENVF" && [ "${1:-}" != "--nova-senha" ]; then
  echo "Senha ja definida. (Para trocar: bash /opt/agente-resumo/vps/configurar.sh --nova-senha)"
else
  echo "Escolha uma senha (minimo 8 caracteres). Ela nao aparece enquanto voce digita."
  LINHA=$(python3 /opt/agente-resumo/interface/app.py --criar-senha | grep '^SENHA_HASH=') || { echo "Senha nao criada."; exit 1; }
  sed -i '/^SENHA_HASH=/d' "$ENVF" && echo "$LINHA" >> "$ENVF"
  echo "Senha salva (so o resumo criptografico fica no servidor)."
fi

passo "2 de 3  CONECTAR O WHATSAPP"
if su - agente -c "WACLI_STORE_DIR=/home/agente/.wacli $W --read-only --json auth status" 2>/dev/null | grep -q '"authenticated":true'; then
  echo "WhatsApp ja conectado."
else
  echo "No celular: WhatsApp > Configuracoes > Dispositivos conectados > Conectar dispositivo."
  echo "Aponte a camera para o QR code abaixo. Espere a primeira sincronizacao terminar."
  echo "(Se o QR ficar cortado, aumente a janela ou diminua o zoom com Ctrl e -)"
  su - agente -c "WACLI_STORE_DIR=/home/agente/.wacli $W auth --history-days 30"
fi

passo "3 de 3  LOGIN DO CLAUDE (para o botao Resumir)"
if su - agente -c "~/.local/bin/claude auth status" 2>/dev/null | grep -q '"loggedIn": *true'; then
  echo "Claude ja conectado."
else
  echo "Vai aparecer um link. Abra no navegador, entre na sua conta do Claude,"
  echo "copie o codigo que aparecer e cole aqui."
  su - agente -c "~/.local/bin/claude auth login"
fi

systemctl enable --now agente-resumo-sync.timer >/dev/null 2>&1
systemctl enable agente-resumo >/dev/null 2>&1
systemctl restart agente-resumo
sleep 2
passo "PRONTO"
systemctl is-active agente-resumo >/dev/null && echo "Agente no ar: https://resumo.andrelink.tech" || { echo "O agente nao subiu. Veja: journalctl -u agente-resumo -n 30"; }
