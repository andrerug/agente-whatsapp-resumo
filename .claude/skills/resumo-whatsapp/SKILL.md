---
name: resumo-whatsapp
description: Lê as mensagens de WhatsApp sincronizadas pelo wacli e entrega um resumo por conversa com pedidos, prazos, decisões e a lista do que fazer. Somente leitura — nunca envia nada. Use quando o usuário disser "/resumo-whatsapp", "resume meu WhatsApp", "o que perdi no grupo X", "o que me pediram hoje no zap", "resumo das mensagens de ontem".
---

# Resumo do WhatsApp

Siga as regras do `CLAUDE.md` desta pasta (somente leitura, `--read-only --json`, LGPD).

## Passos

1. **Período e foco.** Entenda o que o usuário pediu: um período ("hoje", "desde ontem", "esta semana") e, se houver, uma conversa ou um assunto. Sem período informado, use as últimas 24 horas. Converta para a data `AAAA-MM-DD` de início.
2. **Conferir conexão.** Rode `.\bin\wacli.exe --read-only auth status`. Se não estiver conectado, pare e oriente a rodar `1-conectar-whatsapp.cmd`.
3. **Escolher as conversas.**
   - Conversa específica: `chats list --query "trecho do nome"` para achar o nome exato.
   - Visão geral: `chats list --limit 30` e escolha as que tiveram mensagens no período.
4. **Ler.** Para cada conversa escolhida: `messages list --chat "Nome exato" --after AAAA-MM-DD --asc --limit 200`. Para um assunto: `messages search "termo" --after AAAA-MM-DD`.
5. **Resumir** no formato do `CLAUDE.md`: Conversa, Resumo, Pedidos para mim, Prazos e datas, Decisões. Termine com **O que fazer agora**.
6. **Rascunhos de resposta** só se o usuário pedir — escreva o texto no chat para ele copiar. Nunca envie.

## Se faltar mensagem

O WhatsApp entrega o histórico antigo aos poucos. Se o período pedido não tiver mensagens, diga isso e sugira atualizar com `2-atualizar-mensagens.cmd` (o celular precisa estar ligado e com internet).
