"""
Interface local do Agente 1 — Resumo do WhatsApp (somente leitura).

Modo local (Windows): abre em http://127.0.0.1:8765, sem senha, só aceita este computador.
Modo servidor (VPS): roda atrás do Caddy (https) e exige senha.
  Variáveis: HOSTS_PERMITIDOS="resumo.andrelink.tech", SENHA_HASH="pbkdf2$<iter>$<sal hex>$<hash hex>".
Lê as mensagens pelo wacli (sempre --read-only) e resume com o Claude da assinatura
(claude -p, sem ferramentas). Só Python 3 padrão, nada para instalar.
NUNCA envia mensagens: o agente só tem comandos de leitura (ver mcp/whatsapp_leitura_mcp.py).
"""

import glob
import hashlib
import hmac
import json
import mimetypes
import os
import secrets
import time
import subprocess
import sys
import threading
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent
sys.path.insert(0, str(RAIZ / "mcp"))
import whatsapp_leitura_mcp as wa  # noqa: E402  (reaproveita as funções de leitura do MCP)

PORTA = int(os.environ.get("PORTA", "8765"))
HOSTS = {"127.0.0.1", "localhost"} | {h.strip() for h in os.environ.get("HOSTS_PERMITIDOS", "").split(",") if h.strip()}
SENHA_HASH = os.environ.get("SENHA_HASH", "").strip()
MODO_SERVIDOR = bool(os.environ.get("HOSTS_PERMITIDOS"))
SESSOES = {}  # token -> validade (epoch)
DURACAO_SESSAO = 30 * 24 * 3600
FALHAS = {}  # ip -> [epochs]
MAX_LINHAS_RESUMO = 500
# Modelo do resumo: Haiku gasta bem menos da cota da assinatura e resolve pedidos, prazos e decisões.
MODELO_CLAUDE = os.environ.get("MODELO_CLAUDE", "haiku")
TRAVA_SYNC = threading.Lock()

PROMPT_SISTEMA = (
    "Você é um assistente de servidor público que resume mensagens de WhatsApp em português do Brasil. "
    "Você só lê: nunca diga que enviou ou vai enviar mensagens. Para cada conversa, use esta estrutura em Markdown:\n"
    "## Nome da conversa\n**Resumo:** 2 a 3 linhas.\n**Pedidos para mim:** o que pediram ao usuário (as mensagens dele aparecem como 'Eu').\n"
    "**Prazos e datas:** datas, horários e reuniões.\n**Decisões:** o que ficou combinado.\n"
    "Omita um item quando não houver nada. No final, escreva '## O que fazer agora' com uma lista curta, da ação mais urgente para a menos urgente. "
    "Ignore figurinhas, cumprimentos e correntes. Mídia sem texto: diga só quantas houve e de quem, sem inventar o conteúdo. "
    "Não repita números de telefone, CPF ou dados de saúde (LGPD). Seja direto."
)


# ------------------------------------------------------------------ Claude

def achar_claude():
    for nome in ("claude.exe", "claude.cmd", "claude"):
        for pasta in os.environ.get("PATH", "").split(os.pathsep):
            p = Path(pasta) / nome
            if p.is_file():
                return str(p)
    candidatos = glob.glob(os.path.join(os.environ.get("APPDATA", ""), "Claude", "claude-code", "*", "*", "claude.exe"))
    candidatos += glob.glob(os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe"))
    candidatos += [p for p in (os.path.expanduser("~/.local/bin/claude"), "/usr/local/bin/claude") if os.path.isfile(p)]
    if not candidatos:
        return None
    return max(candidatos, key=os.path.getmtime)


def resumir_com_claude(texto, foco):
    exe = achar_claude()
    if not exe:
        raise RuntimeError("SEM_CLAUDE")
    pedido = "Resuma as mensagens abaixo."
    if foco:
        pedido += f" Dê atenção especial a: {foco}."
    entrada = f"{pedido}\n\n<mensagens>\n{texto}\n</mensagens>"
    proc = subprocess.run(
        [exe, "-p", "--model", MODELO_CLAUDE, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
         "--system-prompt", PROMPT_SISTEMA, "--output-format", "text"],
        input=entrada.encode("utf-8"), capture_output=True, timeout=300,
        cwd=str(AQUI), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    saida = proc.stdout.decode("utf-8", "replace").strip()
    erro = proc.stderr.decode("utf-8", "replace").strip()
    if "Not logged in" in (saida + erro) or "/login" in (saida + erro):
        raise RuntimeError("SEM_LOGIN")
    if proc.returncode != 0 or not saida:
        raise RuntimeError(erro or saida or "o Claude não respondeu")
    return saida


# ------------------------------------------------------------------ dados

def conversas(busca="", nao_lidas=False, limite=200):
    args = ["chats", "list", "--limit", str(limite)]
    if busca:
        args += ["--query", busca]
    if nao_lidas:
        args += ["--unread"]
    lista = wa.wacli(*args) or []
    return [
        {"jid": c.get("jid"), "nome": c.get("name") or c.get("jid"),
         "tipo": {"group": "grupo", "dm": "contato"}.get(c.get("kind"), c.get("kind") or ""),
         "ultima": c.get("last_message_ts"), "nao_lidas": c.get("unread_count") or 0}
        for c in lista if (c.get("last_message_ts") or "").startswith("2")
    ]


def mensagens(jid, depois_de, limite=300):
    if jid == "*":
        args = ["messages", "list", "--from-them", "--after", depois_de, "--limit", str(limite)]
        varias = True
    else:
        args = ["messages", "list", "--chat", jid, "--after", depois_de, "--limit", str(limite)]
        varias = False
    msgs, mais, sync = wa.lista_mensagens(wa.wacli(*args))
    itens = []
    for m in reversed(msgs):
        if m.get("ReactionEmoji"):
            continue
        texto = " ".join((m.get("Text") or "").split())
        if not texto and m.get("MediaType"):
            texto = f"[{m['MediaType']}] {m.get('MediaCaption') or ''}".strip()
        if not texto:
            texto = "[mensagem sem texto]"
        itens.append({"hora": wa.hora(m.get("Timestamp", "")), "quem": wa.remetente(m), "eu": bool(m.get("FromMe")),
                      "conversa": m.get("ChatName") or "", "texto": texto})
    linhas = [f"{i['hora']} | {('[' + i['conversa'] + '] ') if varias else ''}{i['quem']}: {i['texto']}" for i in itens]
    return {"itens": itens, "texto": "\n".join(linhas[-MAX_LINHAS_RESUMO:]), "tem_mais": mais, "ultima_sync": wa.hora(sync) if sync else None}


def status():
    try:
        d = wa.wacli("auth", "status") or {}
        conectado = bool(d.get("authenticated"))
    except Exception:
        conectado = False
    return {"conectado": conectado, "claude": bool(achar_claude())}


def atualizar():
    if not TRAVA_SYNC.acquire(blocking=False):
        raise RuntimeError("Já existe uma atualização em andamento.")
    try:
        env = {k: v for k, v in os.environ.items() if k != "WACLI_READONLY"}
        env["WACLI_STORE_DIR"] = os.environ.get("WACLI_STORE_DIR") or str(Path.home() / ".wacli")
        proc = subprocess.run([wa.WACLI, "sync", "--once", "--idle-exit", "15s"], capture_output=True, env=env,
                              timeout=600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(err[-1] if err else "falha ao atualizar")
        return {"ok": True}
    finally:
        TRAVA_SYNC.release()


# ------------------------------------------------------------------ HTTP

def data_inicio(periodo):
    hoje = datetime.now()
    dias = {"hoje": 0, "24h": 1, "3d": 3, "7d": 7, "30d": 30}.get(periodo, 1)
    return (hoje - timedelta(days=dias)).strftime("%Y-%m-%d")


def senha_confere(senha):
    try:
        _, it, sal, h = SENHA_HASH.split("$")
        calc = hashlib.pbkdf2_hmac("sha256", senha.encode("utf-8"), bytes.fromhex(sal), int(it))
        return hmac.compare_digest(calc.hex(), h)
    except Exception:
        return False


ESTATICOS = {"/av/av.css", "/av/fontes/archivo-latin-variavel.woff2"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _ip(self):
        return (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()

    def _sessao_ok(self):
        if not MODO_SERVIDOR:
            return True
        for parte in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = parte.strip().partition("=")
            if k == "sessao" and SESSOES.get(v, 0) > time.time():
                return True
        return False

    def _seguranca(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")

    def _arquivo(self, caminho, tipo):
        corpo = caminho.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-cache" if tipo.startswith("text/html") else "max-age=86400")
        self._seguranca()
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, obj, code=200):
        corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self._seguranca()
        for c in getattr(self, "_cookies", []):
            self.send_header("Set-Cookie", c)
        self.end_headers()
        self.wfile.write(corpo)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in HOSTS

    def do_GET(self):
        if not self._host_ok():
            return self._json({"erro": "acesso negado"}, 403)
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        try:
            if u.path == "/":
                return self._arquivo(AQUI / "index.html", "text/html; charset=utf-8")
            if u.path in ESTATICOS:
                return self._arquivo(AQUI / u.path.lstrip("/"), mimetypes.guess_type(u.path)[0] or "application/octet-stream")
            if u.path == "/api/sessao":
                return self._json({"logado": self._sessao_ok(), "precisa_senha": MODO_SERVIDOR})
            if not self._sessao_ok():
                return self._json({"erro": "SENHA"}, 401)
            if u.path == "/api/status":
                return self._json(status())
            if u.path == "/api/conversas":
                return self._json(conversas(q.get("busca", ""), q.get("nao_lidas") == "1"))
            if u.path == "/api/mensagens":
                return self._json(mensagens(q["jid"], data_inicio(q.get("periodo", "24h"))))
            return self._json({"erro": "não encontrado"}, 404)
        except Exception as e:
            return self._json({"erro": str(e)}, 500)

    def do_POST(self):
        if not self._host_ok() or self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self._json({"erro": "acesso negado"}, 403)
        tam = int(self.headers.get("Content-Length") or 0)
        dados = json.loads(self.rfile.read(min(tam, 100_000)) or b"{}")
        if self.path == "/api/entrar":
            return self._entrar(dados)
        if self.path == "/api/sair":
            for parte in (self.headers.get("Cookie") or "").split(";"):
                k, _, v = parte.strip().partition("=")
                if k == "sessao":
                    SESSOES.pop(v, None)
            self._cookies = ["sessao=; Path=/; Max-Age=0; HttpOnly; Secure; SameSite=Strict"]
            return self._json({"ok": True})
        if not self._sessao_ok():
            return self._json({"erro": "SENHA"}, 401)
        try:
            if self.path == "/api/resumir":
                m = mensagens(dados["jid"], data_inicio(dados.get("periodo", "24h")))
                if not m["itens"]:
                    return self._json({"resumo": "_Nenhuma mensagem nesse período._", "qtd": 0})
                nome = dados.get("nome") or ""
                texto = m["texto"] if dados["jid"] == "*" else f"Conversa: {nome}\n\n{m['texto']}"
                return self._json({"resumo": resumir_com_claude(texto, dados.get("foco", "")), "qtd": len(m["itens"])})
            if self.path == "/api/atualizar":
                return self._json(atualizar())
            return self._json({"erro": "não encontrado"}, 404)
        except Exception as e:
            return self._json({"erro": str(e)}, 500)


    def _entrar(self, dados):
        if not MODO_SERVIDOR:
            return self._json({"ok": True})
        ip, agora = self._ip(), time.time()
        recentes = [t for t in FALHAS.get(ip, []) if agora - t < 900]
        if len(recentes) >= 5:
            return self._json({"erro": "Muitas tentativas. Espere 15 minutos."}, 429)
        if not SENHA_HASH or not senha_confere(str(dados.get("senha", ""))):
            FALHAS[ip] = recentes + [agora]
            time.sleep(1)
            return self._json({"erro": "Senha incorreta."}, 401)
        FALHAS.pop(ip, None)
        token = secrets.token_urlsafe(32)
        SESSOES[token] = agora + DURACAO_SESSAO
        self._cookies = [f"sessao={token}; Path=/; Max-Age={DURACAO_SESSAO}; HttpOnly; Secure; SameSite=Strict"]
        return self._json({"ok": True})


def main():
    if MODO_SERVIDOR and not SENHA_HASH:
        sys.exit("Defina SENHA_HASH antes de rodar no servidor (rode: python3 interface/app.py --criar-senha).")
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Handler)
    url = f"http://127.0.0.1:{PORTA}"
    print(f"Interface aberta em {url}  (feche esta janela para encerrar)")
    if "--sem-navegador" not in sys.argv and not MODO_SERVIDOR:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    srv.serve_forever()


def criar_senha():
    import getpass
    s1 = getpass.getpass("Nova senha da interface (minimo 8 caracteres): ")
    s2 = getpass.getpass("Repita a senha: ")
    if s1 != s2 or len(s1) < 8:
        sys.exit("As senhas nao conferem ou tem menos de 8 caracteres.")
    sal, it = secrets.token_bytes(16), 600_000
    h = hashlib.pbkdf2_hmac("sha256", s1.encode("utf-8"), sal, it)
    print(f"SENHA_HASH=pbkdf2${it}${sal.hex()}${h.hex()}")


if __name__ == "__main__":
    criar_senha() if "--criar-senha" in sys.argv else main()
