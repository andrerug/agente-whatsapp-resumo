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
import re
import shutil
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


# ------------------------------------------------------------------ instâncias (números de WhatsApp)
# Cada número fica numa pasta própria do wacli. O número que já existia continua em WACLI_STORE_DIR
# (id "principal"); os novos ficam em INSTANCIAS_DIR/<id>/store, com um instancia.json só com o nome.
# Conectar um número só faz o pareamento (QR ou código) e baixa o histórico: nada é enviado.

STORE_PRINCIPAL = Path(os.environ.get("WACLI_STORE_DIR") or Path.home() / ".wacli")
INSTANCIAS_DIR = Path(os.environ.get("INSTANCIAS_DIR") or Path.home() / ".wacli-instancias")
NOME_PRINCIPAL = os.environ.get("NOME_PRINCIPAL", "Número principal")
ID_OK = re.compile(r"^[a-z0-9-]{1,40}$")
CONEXOES = {}  # id -> conexão em andamento (QR ou código)
TRAVA_CONEXOES = threading.Lock()
ESPERA_LEITURA = 180     # segundos para ler o QR ou digitar o código no celular
LIMITE_SINCRONIA = 900   # depois de conectado, tempo máximo da primeira sincronização
EVENTOS_DE_ESPERA = {"auth_starting", "warning", "qr_code", "pair_code", "error", "signal"}


def _ler_meta(pasta):
    try:
        return json.loads((pasta / "instancia.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _gravar_meta(pasta, meta):
    (pasta / "instancia.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _inst(iid, pasta):
    meta = _ler_meta(pasta)
    return {"id": iid, "nome": meta.get("nome") or iid, "store": pasta / "store", "nova": bool(meta.get("nova"))}


def lista_instancias():
    itens = []
    if (STORE_PRINCIPAL / "session.db").exists():
        itens.append({"id": "principal", "nome": NOME_PRINCIPAL, "store": STORE_PRINCIPAL, "nova": False})
    if INSTANCIAS_DIR.is_dir():
        pastas = [p for p in INSTANCIAS_DIR.iterdir() if p.is_dir() and ID_OK.match(p.name) and (p / "instancia.json").exists()]
        for pasta in sorted(pastas, key=lambda p: _ler_meta(p).get("criada", "")):
            itens.append(_inst(pasta.name, pasta))
    return itens


def achar_instancia(iid):
    if iid == "principal":
        return {"id": "principal", "nome": NOME_PRINCIPAL, "store": STORE_PRINCIPAL, "nova": False}
    pasta = INSTANCIAS_DIR / (iid or "")
    if not ID_OK.match(iid or "") or not (pasta / "instancia.json").exists():
        raise RuntimeError("Número não encontrado. Volte para a lista de números.")
    return _inst(iid, pasta)


def final_do_numero(fone):
    d = re.sub(r"\D", "", (fone or "").split("@")[0].split(":")[0])
    return d[-4:] if len(d) >= 4 else ""


def estado_instancia(inst):
    """Estado de um número: conectado, desconectado, aguardando (QR na tela) ou sincronizando."""
    base = {"id": inst["id"], "nome": inst["nome"], "final": "", "conectado": False, "estado": "desconectado"}
    c = CONEXOES.get(inst["id"])
    if c and c["estado"] in ("aguardando", "sincronizando"):
        base["estado"] = c["estado"]
        base["conectado"] = c["estado"] == "sincronizando"
        return base
    try:
        d = (wa.wacli("auth", "status", store=inst["store"]) if inst["store"].exists() else {}) or {}
    except Exception:
        d = {}
    base["conectado"] = bool(d.get("authenticated"))
    base["final"] = final_do_numero(d.get("phone") or d.get("linked_jid"))
    base["estado"] = "conectado" if base["conectado"] else "desconectado"
    return base


def instancias_publicas():
    return [estado_instancia(i) for i in lista_instancias()]


def criar_instancia(nome):
    nome = " ".join(str(nome or "").split())[:60]
    if not nome:
        raise RuntimeError("Dê um nome para o número. Exemplo: Celular do protocolo.")
    import unicodedata
    simples = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode().lower()
    base = re.sub(r"[^a-z0-9]+", "-", simples).strip("-")[:24] or "numero"
    iid = f"{base}-{secrets.token_hex(2)}"
    pasta = INSTANCIAS_DIR / iid
    (pasta / "store").mkdir(parents=True, exist_ok=False)
    for p in (INSTANCIAS_DIR, pasta, pasta / "store"):
        try:
            os.chmod(p, 0o700)
        except Exception:
            pass
    _gravar_meta(pasta, {"nome": nome, "criada": datetime.now().isoformat(timespec="seconds"), "nova": True})
    return {"id": iid, "nome": nome}


def _marcar_conectada(iid):
    if iid == "principal":
        return
    pasta = INSTANCIAS_DIR / iid
    meta = _ler_meta(pasta)
    if meta.get("nova"):
        meta.pop("nova", None)
        _gravar_meta(pasta, meta)


def _descartar_se_nunca_conectou(iid):
    """Número criado agora que não chegou a conectar não tem mensagem nenhuma: sai da lista."""
    if iid == "principal":
        return
    pasta = INSTANCIAS_DIR / iid
    if _ler_meta(pasta).get("nova") and pasta.parent == INSTANCIAS_DIR and ID_OK.match(pasta.name):
        shutil.rmtree(pasta, ignore_errors=True)


def conectar(iid, modo, telefone=""):
    inst = achar_instancia(iid)
    with TRAVA_CONEXOES:
        atual = CONEXOES.get(iid)
        if atual and atual["estado"] in ("aguardando", "sincronizando"):
            return conexao_publica(iid)
        if estado_instancia(inst)["conectado"]:
            raise RuntimeError("Este número já está conectado.")
        args = [wa.WACLI, "--events", "--store", str(inst["store"]), "auth", "--history-days", "30", "--idle-exit", "30s"]
        if modo == "telefone":
            fone = re.sub(r"\D", "", telefone or "")
            if not 12 <= len(fone) <= 15:
                raise RuntimeError("Digite o número com 55, o DDD e o número, só algarismos. Exemplo: 5591988887777.")
            args += ["--phone", fone]
        else:
            modo = "qr"
            args += ["--qr-format", "text"]
        inst["store"].mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if k not in ("WACLI_READONLY", "WACLI_STORE_DIR")}
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, env=env,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        c = {"proc": proc, "modo": modo, "estado": "aguardando", "qr": "", "codigo": "", "erro": "",
             "inicio": time.time(), "lido_em": None, "nova": inst["nova"]}
        CONEXOES[iid] = c
    threading.Thread(target=_acompanhar, args=(iid, c, inst), daemon=True).start()
    return conexao_publica(iid)


def _acompanhar(iid, c, inst):
    proc = c["proc"]

    def vigia():  # encerra se ninguém ler o QR a tempo ou se a primeira sincronização passar do limite
        while proc.poll() is None:
            agora = time.time()
            if c["estado"] == "aguardando" and agora - c["inicio"] > ESPERA_LEITURA:
                c["erro"] = c["erro"] or "O tempo acabou antes de o celular confirmar."
                proc.terminate()
            elif c["estado"] == "sincronizando" and agora - (c["lido_em"] or agora) > LIMITE_SINCRONIA:
                proc.terminate()
            time.sleep(2)
    threading.Thread(target=vigia, daemon=True).start()

    for bruta in proc.stderr:
        try:
            ev = json.loads(bruta.decode("utf-8", "replace"))
        except Exception:
            continue
        nome, dados = ev.get("event"), ev.get("data") or {}
        if nome == "qr_code":
            c["qr"] = dados.get("code", "")
        elif nome == "pair_code":
            c["codigo"] = dados.get("code", "")
        elif nome == "error":
            msg = dados.get("message", "")
            if c["estado"] == "aguardando" and not c["erro"] and "context canceled" not in msg:
                c["erro"] = msg
        elif nome not in EVENTOS_DE_ESPERA and c["estado"] == "aguardando":
            # Qualquer outro evento só vem depois que o celular aceitou: começou a primeira sincronização.
            c.update(estado="sincronizando", lido_em=time.time(), qr="", codigo="")
            _marcar_conectada(iid)
    proc.wait()
    try:
        ok = bool((wa.wacli("auth", "status", store=inst["store"]) or {}).get("authenticated"))
    except Exception:
        ok = False
    if ok:
        c["estado"] = "conectado"
        _marcar_conectada(iid)
    else:
        c["estado"] = "erro"
        c["erro"] = c["erro"] or "A conexão não foi concluída."
        if c["nova"]:
            _descartar_se_nunca_conectou(iid)


def conexao_publica(iid):
    c = CONEXOES.get(iid)
    if not c:
        return {"estado": "nenhuma"}
    restante = max(0, int(ESPERA_LEITURA - (time.time() - c["inicio"]))) if c["estado"] == "aguardando" else None
    return {"estado": c["estado"], "modo": c["modo"], "qr": c["qr"], "codigo": c["codigo"], "erro": c["erro"],
            "restante": restante, "descartada": c["estado"] == "erro" and c["nova"]}


def cancelar(iid):
    c = CONEXOES.get(iid)
    if c and c["proc"].poll() is None and c["estado"] == "aguardando":
        c["erro"] = "Conexão cancelada."
        c["proc"].terminate()
        try:
            c["proc"].wait(timeout=8)
        except subprocess.TimeoutExpired:
            c["proc"].kill()
        c["estado"] = "erro"  # a lista já sai certa, sem esperar a outra thread
        if c["nova"]:
            _descartar_se_nunca_conectou(iid)
    elif not c:
        try:
            if achar_instancia(iid)["nova"]:
                _descartar_se_nunca_conectou(iid)
        except RuntimeError:
            pass
    return {"ok": True}


def sincronizar_todas():
    """Usado pelo timer da VPS: baixa as mensagens novas de cada número conectado. Não envia nada."""
    for inst in lista_instancias():
        if not estado_instancia(inst)["conectado"]:
            print(f"{inst['nome']}: desconectado, pulando")
            continue
        try:
            atualizar(inst)
            print(f"{inst['nome']}: atualizado")
        except Exception as e:
            print(f"{inst['nome']}: {e}")


# ------------------------------------------------------------------ dados

def conversas(store, busca="", nao_lidas=False, limite=200):
    args = ["chats", "list", "--limit", str(limite)]
    if busca:
        args += ["--query", busca]
    if nao_lidas:
        args += ["--unread"]
    lista = wa.wacli(*args, store=store) or []
    return [
        {"jid": c.get("jid"), "nome": c.get("name") or c.get("jid"),
         "tipo": {"group": "grupo", "dm": "contato"}.get(c.get("kind"), c.get("kind") or ""),
         "ultima": c.get("last_message_ts"), "nao_lidas": c.get("unread_count") or 0}
        for c in lista if (c.get("last_message_ts") or "").startswith("2")
    ]


def mensagens(store, jid, depois_de, limite=300):
    if jid == "*":
        args = ["messages", "list", "--from-them", "--after", depois_de, "--limit", str(limite)]
        varias = True
    else:
        args = ["messages", "list", "--chat", jid, "--after", depois_de, "--limit", str(limite)]
        varias = False
    msgs, mais, sync = wa.lista_mensagens(wa.wacli(*args, store=store))
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
    return {"claude": bool(achar_claude())}


def atualizar(inst):
    c = CONEXOES.get(inst["id"])
    if c and c["estado"] in ("aguardando", "sincronizando"):
        raise RuntimeError("Este número ainda está terminando a conexão. Espere alguns minutos.")
    if not TRAVA_SYNC.acquire(blocking=False):
        raise RuntimeError("Já existe uma atualização em andamento.")
    try:
        env = {k: v for k, v in os.environ.items() if k != "WACLI_READONLY"}
        env["WACLI_STORE_DIR"] = str(inst["store"])
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


ESTATICOS = {"/av/av.css", "/av/fontes/archivo-latin-variavel.woff2", "/av/qrcode.js"}


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
            if u.path == "/api/instancias":
                return self._json(instancias_publicas())
            if u.path == "/api/conexao":
                return self._json(conexao_publica(q.get("inst", "")))
            if u.path == "/api/conversas":
                inst = achar_instancia(q.get("inst", ""))
                return self._json(conversas(inst["store"], q.get("busca", ""), q.get("nao_lidas") == "1"))
            if u.path == "/api/mensagens":
                inst = achar_instancia(q.get("inst", ""))
                return self._json(mensagens(inst["store"], q["jid"], data_inicio(q.get("periodo", "24h"))))
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
            if self.path == "/api/instancias":
                return self._json(criar_instancia(dados.get("nome")))
            if self.path == "/api/conectar":
                return self._json(conectar(str(dados.get("inst", "")), dados.get("modo"), str(dados.get("telefone", ""))))
            if self.path == "/api/cancelar":
                return self._json(cancelar(str(dados.get("inst", ""))))
            if self.path == "/api/resumir":
                inst = achar_instancia(str(dados.get("inst", "")))
                m = mensagens(inst["store"], dados["jid"], data_inicio(dados.get("periodo", "24h")))
                if not m["itens"]:
                    return self._json({"resumo": "_Nenhuma mensagem nesse período._", "qtd": 0})
                nome = dados.get("nome") or ""
                texto = m["texto"] if dados["jid"] == "*" else f"Conversa: {nome}\n\n{m['texto']}"
                return self._json({"resumo": resumir_com_claude(texto, dados.get("foco", "")), "qtd": len(m["itens"])})
            if self.path == "/api/atualizar":
                return self._json(atualizar(achar_instancia(str(dados.get("inst", "")))))
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
    if "--criar-senha" in sys.argv:
        criar_senha()
    elif "--sincronizar-todas" in sys.argv:
        sincronizar_todas()
    else:
        main()
