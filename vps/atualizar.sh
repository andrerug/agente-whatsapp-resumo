#!/bin/bash
# Atualização automática do Agente 1 na VPS (roda a cada 2 minutos pelo timer agente-resumo-deploy).
# Busca o repositório privado com uma chave SÓ DE LEITURA; se houver commit novo em main,
# atualiza o código e reinicia o agente. Nada do servidor (senha, sessão do WhatsApp) vai para o GitHub.
set -euo pipefail
cd /opt/agente-resumo
export GIT_SSH_COMMAND="ssh -i /root/.ssh/deploy_agente_resumo -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

git fetch -q origin main
ATUAL=$(git rev-parse HEAD)
NOVO=$(git rev-parse origin/main)
[ "$ATUAL" = "$NOVO" ] && exit 0

echo "Atualizando $ATUAL -> $NOVO"
git reset -q --hard origin/main
chown -R root:agente /opt/agente-resumo && chmod -R g+rX,o-rwx /opt/agente-resumo
chmod 750 vps/*.sh

# Testa antes de reiniciar: se o código novo tiver erro de sintaxe, volta para o anterior.
if ! python3 -m py_compile interface/app.py mcp/whatsapp_leitura_mcp.py; then
  echo "Erro no código novo; voltando para $ATUAL"
  git reset -q --hard "$ATUAL"
  exit 1
fi

# Unidades do systemd versionadas no repositório
for u in agente-resumo.service agente-resumo-sync.service agente-resumo-sync.timer agente-resumo-deploy.service agente-resumo-deploy.timer; do
  [ -f "vps/$u" ] && ! cmp -s "vps/$u" "/etc/systemd/system/$u" && cp "vps/$u" "/etc/systemd/system/$u" && RECARREGAR=1
done
[ "${RECARREGAR:-}" = 1 ] && systemctl daemon-reload

systemctl restart agente-resumo
sleep 2
systemctl is-active --quiet agente-resumo && echo "No ar: $(git log -1 --format='%h %s')"
