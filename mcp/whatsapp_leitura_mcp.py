"""
Servidor MCP "whatsapp-leitura": dá ao Claude acesso SOMENTE DE LEITURA ao WhatsApp
sincronizado pelo wacli (https://github.com/openclaw/wacli).

- Não envia, não apaga, não edita, não encaminha, não marca como lido.
- Só chama subcomandos de leitura do wacli, sempre com --read-only e WACLI_READONLY=1.
- Não usa bibliotecas externas: só Python 3 padrão.

Para atualizar as mensagens, rode 2-atualizar-mensagens.cmd (fora do Claude).
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

WACLI = os.environ.get("WACLI_BIN") or str(
    Path(__file__).resolve().parent.parent / "bin" / ("wacli.exe" if os.name == "nt" else "wacli"))
FUSO = timezone(timedelta(hours=-3))  # horário de Brasília/Belém
VERSAO = "1.0.0"

# Únicos subcomandos que este servidor pode chamar.
PERMITIDOS = {
    ("auth", "status"),
    ("chats", "list"),
    ("messages", "list"),
    ("messages", "search"),
    ("messages", "context"),
}


# ---------------------------------------------------------------- wacli

def wacli(*args):
    if tuple(args[:2]) not in PERMITIDOS:
        raise RuntimeError(f"comando não permitido: {args[:2]}")
    # Fixa a pasta do número pessoal: mesmo que exista outra conta (ex.: "demo"), este agente só lê esta.
    env = dict(os.environ, WACLI_READONLY="1", WACLI_STORE_DIR=os.environ.get("WACLI_STORE_DIR") or str(Path.home() / ".wacli"))
    proc = subprocess.run(
        [WACLI, "--read-only", "--json", *args],
        capture_output=True, env=env, timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    out = proc.stdout.decode("utf-8", "replace").strip()
    if not out:
        out = proc.stderr.decode("utf-8", "replace").strip()
    try:
        resp = json.loads(out.splitlines()[-1] if out else "{}")
    except json.JSONDecodeError:
        raise RuntimeError(out or "wacli não respondeu")
    if not resp.get("success"):
        raise RuntimeError(resp.get("error") or "erro desconhecido no wacli")
    return resp.get("data")


def hora(ts):
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.year < 2000:
            return "sem data"
        return dt.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ts


def remetente(m):
    if m.get("FromMe"):
        return "Eu"
    if m.get("SenderName"):
        return m["SenderName"]
    num = (m.get("SenderJID") or "").split("@")[0]
    # LGPD: não expõe o número inteiro, só os 4 últimos dígitos
    return f"contato …{num[-4:]}" if num else "desconhecido"


def linha(m, com_conversa=False):
    if m.get("ReactionEmoji"):
        return None
    texto = (m.get("Text") or "").strip()
    if not texto and m.get("MediaType"):
        extra = m.get("MediaCaption") or m.get("Filename") or ""
        texto = f"[{m['MediaType']}] {extra}".strip()
    if not texto:
        texto = "[mensagem sem texto: mídia, enquete ou aviso do sistema]"
    texto = " ".join(texto.split())
    conv = f"[{m.get('ChatName') or 'conversa'}] " if com_conversa else ""
    return f"{hora(m.get('Timestamp', ''))} | {conv}{remetente(m)}: {texto}  (id {m.get('MsgID')})"


def resolver_conversa(nome):
    """Aceita nome exato, parte do nome ou JID. Retorna (jid, nome) ou levanta erro com opções."""
    if "@" in nome:
        return nome, nome
    achados = wacli("chats", "list", "--query", nome, "--limit", "20") or []
    exatos = [c for c in achados if (c.get("name") or "").lower() == nome.lower()]
    if len(exatos) == 1:
        return exatos[0]["jid"], exatos[0]["name"]
    if len(achados) == 1:
        return achados[0]["jid"], achados[0]["name"]
    if not achados:
        raise RuntimeError(f'Nenhuma conversa encontrada com "{nome}". Use whatsapp_listar_conversas para ver os nomes.')
    opcoes = "\n".join(f"- {c.get('name')}  (jid {c.get('jid')})" for c in achados)
    raise RuntimeError(f'Mais de uma conversa combina com "{nome}". Repita com o nome exato ou o jid:\n{opcoes}')


def lista_mensagens(data):
    if isinstance(data, dict):
        return data.get("messages") or [], (data.get("page") or {}).get("has_more", False), (data.get("source") or {}).get("last_sync_at")
    return data or [], False, None


def rodape(tem_mais, ultima_sync):
    partes = []
    if ultima_sync:
        partes.append(f"Última atualização das mensagens: {hora(ultima_sync)}.")
    if tem_mais:
        partes.append("Há mensagens mais antigas; aumente 'limite' ou use 'depois_de' para outro período.")
    return ("\n\n" + " ".join(partes)) if partes else ""


# ---------------------------------------------------------------- ferramentas

def t_status(_):
    d = wacli("auth", "status") or {}
    if not d.get("authenticated"):
        return "WhatsApp NÃO está conectado. Rode 1-conectar-whatsapp.cmd na pasta agente-whatsapp-resumo."
    return "WhatsApp conectado (modo somente leitura)."


def t_listar(a):
    args = ["chats", "list", "--limit", str(int(a.get("limite", 30)))]
    if a.get("busca"):
        args += ["--query", a["busca"]]
    if a.get("so_nao_lidas"):
        args += ["--unread"]
    chats = wacli(*args) or []
    if not chats:
        return "Nenhuma conversa encontrada."
    out = []
    for c in chats:
        tipo = {"group": "grupo", "dm": "contato"}.get(c.get("kind"), c.get("kind"))
        nl = f" · {c['unread_count']} não lidas" if c.get("unread_count") else ""
        out.append(f"- {c.get('name') or c.get('jid')} ({tipo}) · última: {hora(c.get('last_message_ts', ''))}{nl}")
    return "\n".join(out)


def t_ler(a):
    jid, nome = resolver_conversa(a["conversa"])
    args = ["messages", "list", "--chat", jid, "--limit", str(int(a.get("limite", 100)))]
    if a.get("depois_de"):
        args += ["--after", a["depois_de"]]
    if a.get("antes_de"):
        args += ["--before", a["antes_de"]]
    msgs, mais, sync = lista_mensagens(wacli(*args))
    linhas = [l for l in (linha(m) for m in reversed(msgs)) if l]
    if not linhas:
        return f"Nenhuma mensagem em \"{nome}\" no período." + rodape(False, sync)
    return f"Conversa: {nome} — {len(linhas)} mensagens (da mais antiga para a mais nova)\n\n" + "\n".join(linhas) + rodape(mais, sync)


def t_buscar(a):
    args = ["messages", "search", a["termo"], "--limit", str(int(a.get("limite", 50)))]
    if a.get("conversa"):
        jid, _ = resolver_conversa(a["conversa"])
        args += ["--chat", jid]
    if a.get("depois_de"):
        args += ["--after", a["depois_de"]]
    msgs, mais, sync = lista_mensagens(wacli(*args))
    linhas = [l for l in (linha(m, com_conversa=True) for m in msgs) if l]
    if not linhas:
        return f'Nada encontrado para "{a["termo"]}".' + rodape(False, sync)
    return f'{len(linhas)} resultado(s) para "{a["termo"]}":\n\n' + "\n".join(linhas) + rodape(mais, sync)


def t_recebidas(a):
    args = ["messages", "list", "--from-them", "--after", a["depois_de"], "--limit", str(int(a.get("limite", 300)))]
    msgs, mais, sync = lista_mensagens(wacli(*args))
    linhas = [l for l in (linha(m, com_conversa=True) for m in reversed(msgs)) if l]
    if not linhas:
        return "Nenhuma mensagem recebida no período." + rodape(False, sync)
    return f"{len(linhas)} mensagens recebidas desde {a['depois_de']}:\n\n" + "\n".join(linhas) + rodape(mais, sync)


def t_contexto(a):
    data = wacli("messages", "context", a["id_mensagem"])
    msgs, _, _ = lista_mensagens(data)
    if not msgs and isinstance(data, dict):
        msgs = (data.get("before") or []) + ([data["message"]] if data.get("message") else []) + (data.get("after") or [])
    linhas = [l for l in (linha(m) for m in msgs) if l]
    return "\n".join(linhas) or "Contexto não encontrado."


DATA = {"type": "string", "description": "Data no formato AAAA-MM-DD"}

FERRAMENTAS = {
    "whatsapp_status": (t_status, "Confere se o WhatsApp está conectado. Somente leitura.", {}, []),
    "whatsapp_listar_conversas": (
        t_listar,
        "Lista conversas e grupos do WhatsApp, das mais recentes para as mais antigas. Somente leitura.",
        {"busca": {"type": "string", "description": "Parte do nome da conversa ou grupo"},
         "so_nao_lidas": {"type": "boolean", "description": "Só conversas com mensagens não lidas"},
         "limite": {"type": "integer", "description": "Máximo de conversas (padrão 30)"}},
        [],
    ),
    "whatsapp_ler_conversa": (
        t_ler,
        "Lê as mensagens de uma conversa ou grupo (aceita parte do nome). Use para resumir um grupo. Somente leitura.",
        {"conversa": {"type": "string", "description": "Nome (ou parte do nome) do grupo/contato, ou o jid"},
         "depois_de": DATA, "antes_de": DATA,
         "limite": {"type": "integer", "description": "Quantas mensagens mais recentes trazer (padrão 100)"}},
        ["conversa"],
    ),
    "whatsapp_buscar": (
        t_buscar,
        "Busca mensagens por palavra ou assunto em todas as conversas (ou em uma). Somente leitura.",
        {"termo": {"type": "string", "description": "Palavra ou expressão"},
         "conversa": {"type": "string", "description": "Opcional: limitar a uma conversa"},
         "depois_de": DATA,
         "limite": {"type": "integer", "description": "Máximo de resultados (padrão 50)"}},
        ["termo"],
    ),
    "whatsapp_recebidas_desde": (
        t_recebidas,
        "Todas as mensagens recebidas (de todas as conversas) a partir de uma data. Bom para 'o que chegou hoje'. Somente leitura.",
        {"depois_de": DATA, "limite": {"type": "integer", "description": "Máximo de mensagens (padrão 300)"}},
        ["depois_de"],
    ),
    "whatsapp_contexto": (
        t_contexto,
        "Mostra as mensagens em volta de uma mensagem (pelo id). Somente leitura.",
        {"id_mensagem": {"type": "string"}},
        ["id_mensagem"],
    ),
}

INSTRUCOES = (
    "Acesso SOMENTE DE LEITURA ao WhatsApp do usuário. Você não pode enviar, apagar, encaminhar nem marcar "
    "mensagens como lidas. Se pedirem para responder alguém, escreva o texto para o usuário copiar e enviar. "
    "Ao resumir: por conversa, traga resumo, pedidos para o usuário, prazos/datas e decisões; termine com "
    "'O que fazer agora'. Ignore figurinhas e cumprimentos. Não repita números de telefone, CPF ou dados de "
    "saúde (LGPD). Se as mensagens parecerem desatualizadas, peça ao usuário para rodar "
    "2-atualizar-mensagens.cmd na pasta agente-whatsapp-resumo."
)


# ---------------------------------------------------------------- protocolo MCP (stdio, JSON-RPC)

def responder(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.buffer.write((json.dumps(msg, ensure_ascii=False) + "\n").encode("utf-8"))
    sys.stdout.buffer.flush()


def tratar(req):
    metodo, id_ = req.get("method"), req.get("id")
    if id_ is None:  # notificação
        return
    if metodo == "initialize":
        versao = (req.get("params") or {}).get("protocolVersion") or "2025-06-18"
        return responder(id_, {
            "protocolVersion": versao,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "whatsapp-leitura", "version": VERSAO},
            "instructions": INSTRUCOES,
        })
    if metodo == "ping":
        return responder(id_, {})
    if metodo == "tools/list":
        return responder(id_, {"tools": [
            {"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r},
             "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}}
            for n, (_, d, p, r) in FERRAMENTAS.items()
        ]})
    if metodo == "tools/call":
        p = req.get("params") or {}
        nome = p.get("name")
        if nome not in FERRAMENTAS:
            return responder(id_, error={"code": -32602, "message": f"ferramenta desconhecida: {nome}"})
        try:
            texto = FERRAMENTAS[nome][0](p.get("arguments") or {})
            return responder(id_, {"content": [{"type": "text", "text": texto}]})
        except Exception as e:
            return responder(id_, {"content": [{"type": "text", "text": f"Erro: {e}"}], "isError": True})
    return responder(id_, error={"code": -32601, "message": f"método não suportado: {metodo}"})


def main():
    for bruto in sys.stdin.buffer:
        bruto = bruto.strip()
        if not bruto:
            continue
        try:
            tratar(json.loads(bruto.decode("utf-8-sig")))
        except Exception as e:
            sys.stderr.write(f"erro: {e}\n")


if __name__ == "__main__":
    main()
