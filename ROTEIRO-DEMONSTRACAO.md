# Demonstração: agente que lê e resume o WhatsApp

**Mensagem central para a turma:** a IA pode ler e organizar o que chega no WhatsApp, mas quem responde é o servidor. Aqui, o agente **só lê**.

Ferramenta: [wacli](https://github.com/openclaw/wacli) (licença MIT, criado por Peter Steinberger, do OpenClaw). Ele se conecta ao WhatsApp como um "dispositivo conectado", igual ao WhatsApp Web, e guarda as mensagens num banco de dados **no próprio computador**.

## Antes da aula (uns 15 minutos, em casa)

1. Use um **número de demonstração** (um chip só para o curso), não o seu número pessoal nem o do órgão.
2. Coloque esse número em 2 ou 3 grupos de teste e troque mensagens realistas: pedido de ofício com prazo, convocação de reunião, dúvida de um cidadão, um áudio, uma figurinha e um "bom dia" (para mostrar que o agente ignora o ruído).
3. Dois cliques em `1-conectar-whatsapp.cmd` e leia o QR code com o celular.
4. Copie `permissoes-somente-leitura.json` para `.claude\settings.json` (crie a pasta `.claude` se não existir). Assim as travas de permissão também ficam ligadas.
5. Teste uma vez: abra o Claude Code nesta pasta e peça `/resumo-whatsapp de hoje`.

## Na aula (cerca de 20 minutos)

| Tempo | O que fazer | O que dizer |
| --- | --- | --- |
| 3 min | Mostrar os grupos de teste no celular, com dezenas de mensagens | "Quem nunca voltou de uma reunião com 200 mensagens no grupo?" |
| 2 min | Dois cliques em `2-atualizar-mensagens.cmd` | "Isso só baixa as mensagens para o computador. Nada sai daqui." |
| 5 min | No Claude: `/resumo-whatsapp de hoje` | Mostrar pedidos, prazos, decisões e a lista "O que fazer agora" |
| 3 min | Pedir: "o que me pediram sobre ofício esta semana?" | Busca por assunto em todas as conversas |
| 4 min | Pedir: "manda um ok para o grupo" | O agente **recusa** e oferece o texto para copiar. Explicar as 3 travas abaixo |
| 3 min | Perguntas e riscos | Ver a seção de cuidados |

## As 3 travas de "somente leitura" (bom ponto didático)

1. **Trava no programa:** o agente sempre usa `--read-only`. Nesse modo o wacli recusa qualquer envio ("read-only mode: command would intentionally modify WhatsApp"). Isso foi testado.
2. **Trava nas permissões do Claude:** o arquivo `.claude\settings.json` proíbe os comandos de envio, de apagar, de encaminhar e de marcar como lido.
3. **Trava nas instruções:** o `CLAUDE.md` diz o papel do agente. Se o usuário pedir uma resposta, o agente escreve o texto para ele mesmo enviar.

Lição para levar ao serviço público: não basta pedir para a IA "não fazer". O certo é **tirar dela a capacidade** de fazer.

## Cuidados que precisam ser ditos

- **Não é oficial.** O wacli usa o protocolo do WhatsApp Web, que a Meta não autoriza para automação. Só para leitura o risco é baixo, mas existe a chance de o número ser bloqueado. Por isso, use um número de teste.
- **LGPD:** mensagens de cidadãos e colegas são dados pessoais. Para usar isso de verdade no órgão, é preciso base legal, ciência da chefia e do encarregado de dados (DPO), e de preferência a API oficial.
- **Onde ficam os dados:** as mensagens ficam em `C:\Users\<usuário>\.wacli`, no computador. O que vai para a IA é só o trecho que o agente lê para resumir.
- **Para desconectar no fim do curso:** no celular, abra Dispositivos conectados e remova o "wacli". Depois apague a pasta `.wacli`.

## Ponte para o segundo agente

Este agente **lê e resume para o servidor**. O próximo, o agente de atendimento com base de conhecimento (RAG), **conversa com o cidadão**: responde sobre os serviços, faz a triagem e encaminha ao servidor certo. Esse precisa da **API oficial do WhatsApp (Cloud API)**, porque vai enviar mensagens.
