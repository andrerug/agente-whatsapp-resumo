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

## Vários números (desde 15/11/2026)

- A primeira tela lista os números (instâncias) e diz se cada um está conectado.
- **Conectar número** cria um número novo e mostra o QR code na página. Quem não puder usar a câmera escolhe **Código pelo número**: digita o número com 55 e DDD, e a página mostra um código de 8 letras para digitar no celular, em Dispositivos conectados > Conectar dispositivo > Conectar com número de telefone.
- O número antigo continua em `/home/agente/.wacli` (aparece como "Número principal"; o nome muda com `NOME_PRINCIPAL=` em `/etc/agente-resumo.env`). Os novos ficam em `/home/agente/.wacli-instancias/<id>/store`.
- O timer `agente-resumo-sync` agora roda `interface/app.py --sincronizar-todas`, que atualiza todos os números conectados.
- Número novo que não chegou a conectar (cancelado ou com o tempo esgotado) sai da lista sozinho. Para tirar um número conectado, desconecte o aparelho no celular.

## Tarefas de manutenção

- Trocar a senha, reconectar o WhatsApp ou refazer o login do Claude:
  `ssh -t -i ~/.ssh/vps_laboratorio root@187.53.96.1 bash /opt/agente-resumo/vps/configurar.sh --nova-senha`
- Trocar o modelo do resumo: em `/etc/agente-resumo.env`, `MODELO_CLAUDE=haiku` (ou `sonnet`), depois `systemctl restart agente-resumo`.
- Desconectar o WhatsApp do servidor: no celular, Dispositivos conectados, remover o aparelho.
