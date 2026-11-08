# Agente 1 na VPS (resumo.andrelink.tech)

## Como uma mudança chega no ar

1. Edite o código no computador e faça `git push` para `main`.
2. Em até 2 minutos a VPS percebe o commit novo (timer `agente-resumo-deploy`), atualiza e reinicia o agente.
3. Se o código novo tiver erro de sintaxe em Python, a VPS volta sozinha para a versão anterior.

Ver o que aconteceu: `ssh -i ~/.ssh/vps_laboratorio root@187.53.96.1 journalctl -u agente-resumo-deploy -n 20`

## O que fica só no servidor (nunca no GitHub)

| O quê | Onde |
|---|---|
| Senha da página (só o hash PBKDF2) | `/etc/agente-resumo.env` |
| Sessão e mensagens do WhatsApp | `/home/agente/.wacli` |
| Login do Claude | `/home/agente/.claude` |
| Binário do wacli (release oficial, sha256 conferido) | `/opt/agente-resumo/bin/wacli` |
| Chave só de leitura do repositório | `/root/.ssh/deploy_agente_resumo` |

## Tarefas de manutenção

- Trocar a senha, reconectar o WhatsApp ou refazer o login do Claude:
  `ssh -t -i ~/.ssh/vps_laboratorio root@187.53.96.1 bash /opt/agente-resumo/vps/configurar.sh --nova-senha`
- Trocar o modelo do resumo: em `/etc/agente-resumo.env`, `MODELO_CLAUDE=haiku` (ou `sonnet`), depois `systemctl restart agente-resumo`.
- Desconectar o WhatsApp do servidor: no celular, Dispositivos conectados, remover o aparelho.
