#!/usr/bin/env python3
"""
OneDrive CLI via Microsoft Graph API
Uso:
  python getonefiles.py --list
  python getonefiles.py --list "Documentos/HS-STG-02"
  python getonefiles.py --upload /caminho/local/arquivo.txt "Documentos/HS-STG-02/arquivo.txt"
  python getonefiles.py --upload /caminho/local/arquivo.txt "Documentos/HS-STG-02/"
  python getonefiles.py --download "Documentos/HS-STG-02/Full/arquivo.tar" /mnt/Local/destino/
  python getonefiles.py --download "Documentos/HS-STG-02/Full" /mnt/Local/destino/
  python getonefiles.py --delete "Documentos/HS-STG-02/Full/arquivo.txt"
  python getonefiles.py --delete "Documentos/HS-STG-02/Full"
  python getonefiles.py --delete-contents "Documentos/HS-STG-02/Full"
  python getonefiles.py --sync /mnt/Local/Backup "Documentos/HS-STG-02"
  python getonefiles.py --sync /mnt/Local/Backup "Documentos/HS-STG-02" --mirror
  python getonefiles.py --sync /mnt/Local/Backup "Documentos/HS-STG-02" --log-dir /tmp/logs
"""

import os
import sys
import time
import socket
import threading
import logging
import argparse
from datetime import datetime
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.connection import allowed_gai_family
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------
# Configuracao
# ---------------------------------------------

CLIENT_ID       = os.getenv("CLIENT_ID")
CLIENT_SECRET   = os.getenv("CLIENT_SECRET")
TENANT_ID       = os.getenv("TENANT_ID")
USER_ID         = os.getenv("USER_ID")
DEFAULT_LOG_DIR = os.getenv("LOG_DIR", "/var/log/getonefiles")

# Limite de velocidade em MB/s (0 = sem limite)
# Pode ser definido no .env como SPEED_LIMIT=5mb ou SPEED_LIMIT=5
_raw_speed       = os.getenv("SPEED_LIMIT", "0").lower().replace("mb", "").strip()
DEFAULT_SPEED_MB = float(_raw_speed) if _raw_speed else 0.0

GRAPH_BASE       = "https://graph.microsoft.com/v1.0"
TOKEN_URL        = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
UPLOAD_THRESHOLD = 4 * 1024 * 1024    # arquivos >= 4 MB usam upload session

# Limites de chunk para upload session
CHUNK_SIZE_MAX = 1000 * 1024 * 1024   # 1000 MB: padrao sem speed limit
CHUNK_SIZE_MIN = 320 * 1024           # 320 KB: multiplo minimo exigido pelo Graph API

# Logger global (configurado em setup_logging)
log = logging.getLogger("getonefiles")

# Limite de velocidade ativo na execucao (MB/s); 0 = sem limite
_speed_limit_mbs: float = 0.0

# Forcar IPv6 (padrao: somente IPv4)
_force_ipv6: bool = False


def calcular_chunk_size() -> int:
    """
    Calcula o tamanho ideal do chunk para upload session com base no speed limit.

    Sem limite (--speed nao definido):
      Usa CHUNK_SIZE_MAX (1000 MB), minimizando round-trips e aproveitando
      ao maximo a banda disponivel. Para a maioria dos arquivos, o upload
      ocorre em um unico chunk sem interrupcoes.

    Com limite (--speed X):
      Calcula um chunk que leva aproximadamente CHUNK_DURATION_S segundos
      para ser transferido na velocidade alvo. Isso garante que o throttle
      tenha granularidade adequada — chunks pequenos demais causam overhead
      de round-trips; chunks grandes demais tornam o throttle impreciso.

    O resultado e sempre alinhado ao multiplo de 320 KB exigido pelo Graph API
    e limitado ao intervalo [CHUNK_SIZE_MIN, CHUNK_SIZE_MAX].
    """
    CHUNK_DURATION_S = 2.0  # segundos-alvo por chunk quando com speed limit

    if _speed_limit_mbs <= 0:
        return CHUNK_SIZE_MAX

    # Bytes ideais para CHUNK_DURATION_S segundos na velocidade alvo
    ideal = int(_speed_limit_mbs * 1024 * 1024 * CHUNK_DURATION_S)

    # Alinha para baixo ao multiplo de 320 KB mais proximo
    alinhado = max(CHUNK_SIZE_MIN, (ideal // CHUNK_SIZE_MIN) * CHUNK_SIZE_MIN)

    return min(alinhado, CHUNK_SIZE_MAX)


# ---------------------------------------------
# Throttle
# ---------------------------------------------

def aplicar_throttle(bytes_transferidos: int, tempo_inicio: float):
    """
    Pausa o tempo necessario para respeitar o limite de velocidade.
    Baseado no total acumulado transferido desde tempo_inicio.
    """
    if _speed_limit_mbs <= 0:
        return

    limite_bytes_s  = _speed_limit_mbs * 1024 * 1024
    tempo_esperado  = bytes_transferidos / limite_bytes_s
    tempo_decorrido = time.time() - tempo_inicio
    espera          = tempo_esperado - tempo_decorrido

    if espera > 0:
        time.sleep(espera)


def ler_com_throttle(f, tamanho: int) -> bytes:
    """
    Le 'tamanho' bytes do arquivo em sub-chunks de 256 KB,
    aplicando throttle continuo entre cada sub-chunk.
    Garante que a limitacao de velocidade seja aplicada em tempo real
    em vez de apenas como media por chunk grande.
    Sem speed limit: faz f.read() direto, sem nenhum overhead.
    """
    if _speed_limit_mbs <= 0:
        return f.read(tamanho)

    SUB_CHUNK    = 256 * 1024  # 256 KB
    dados        = b""
    tempo_inicio = time.time()
    lido         = 0

    while lido < tamanho:
        parte = f.read(min(SUB_CHUNK, tamanho - lido))
        if not parte:
            break
        dados += parte
        lido  += len(parte)
        aplicar_throttle(lido, tempo_inicio)

    return dados


# ---------------------------------------------
# Networking - IPv4 por padrao
# ---------------------------------------------

class IPv4Adapter(HTTPAdapter):
    """
    HTTPAdapter que restringe conexoes a IPv4 (AF_INET).
    Ativo por padrao; desativado apenas quando --ipv6 ou IPV6=true no .env.
    """
    def send(self, *args, **kwargs):
        import urllib3.util.connection as _conn
        _orig = _conn.allowed_gai_family

        _conn.allowed_gai_family = lambda: socket.AF_INET
        try:
            return super().send(*args, **kwargs)
        finally:
            _conn.allowed_gai_family = _orig


def criar_session() -> requests.Session:
    """Cria uma requests.Session com IPv4Adapter montado, salvo se IPv6 ativo."""
    s = requests.Session()
    if not _force_ipv6:
        adapter = IPv4Adapter()
        s.mount("https://", adapter)
        s.mount("http://",  adapter)
    return s


# Session HTTP global reutilizada em todas as requisicoes
_session: requests.Session = requests.Session()


# ---------------------------------------------
# Logging
# ---------------------------------------------

def setup_logging(log_dir: str):
    """
    Configura o logger para gravar simultaneamente no terminal e em arquivo.
    Nome do arquivo: getonefiles_YYYY-MM-DD_HH-MM-SS.log
    """
    try:
        os.makedirs(log_dir, exist_ok=True)
    except PermissionError:
        print(f"[ERRO] Sem permissao para criar o diretorio de log: {log_dir}")
        sys.exit(1)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file  = os.path.join(log_dir, f"getonefiles_{timestamp}.log")

    log.setLevel(logging.DEBUG)

    fmt     = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt_con = logging.Formatter("%(message)s")  # terminal: sem timestamp

    # Handler: arquivo
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Handler: terminal (apenas INFO+)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(fmt_con)

    log.addHandler(fh)
    log.addHandler(ch)

    log.info(f"=== Inicio da execucao | log: {log_file} ===")
    return log_file


def log_info(msg: str):
    log.info(msg)


def log_ok(msg: str):
    log.info(f"[OK] {msg}")


def log_erro(msg: str):
    log.error(f"[ERRO] {msg}")


def log_aviso(msg: str):
    log.warning(f"[AVISO] {msg}")


def log_secao(titulo: str):
    log.info(f"\n{'='*60}\n{titulo}\n{'='*60}")


# ---------------------------------------------
# Gerenciamento de token
# ---------------------------------------------

_token_cache = {"access_token": None, "expires_at": 0}


def get_token() -> str:
    if _token_cache["access_token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["access_token"]

    payload = {
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope":         "https://graph.microsoft.com/.default",
        "grant_type":    "client_credentials",
    }

    resp = _session.post(TOKEN_URL, data=payload)
    if resp.status_code != 200:
        log_erro(f"Falha ao obter token: {resp.status_code} - {resp.text}")
        sys.exit(1)

    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"]   = time.time() + data["expires_in"]
    log.debug("Token obtido/renovado com sucesso.")
    return _token_cache["access_token"]


def headers() -> dict:
    return {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type":  "application/json",
    }


# ---------------------------------------------
# Helpers
# ---------------------------------------------

def handle_error(resp: requests.Response, acao: str, fatal: bool = True):
    mensagens = {
        400: "Requisicao invalida (400). Verifique o caminho ou os dados enviados.",
        401: "Nao autorizado (401). Verifique CLIENT_ID e CLIENT_SECRET no .env.",
        403: "Sem permissao (403). Verifique as permissoes do aplicativo no Azure.",
        404: "Arquivo ou pasta nao encontrado (404). Verifique o caminho informado.",
        409: "Conflito (409). O item ja existe ou ha um conflito no servidor.",
    }
    msg = mensagens.get(resp.status_code, f"Erro inesperado ({resp.status_code}): {resp.text}")
    log_erro(f"{acao}: {msg}")
    if fatal:
        sys.exit(1)


def drive_url(caminho_remoto: str = None) -> str:
    base = f"{GRAPH_BASE}/users/{USER_ID}/drive"
    if caminho_remoto:
        caminho_remoto = caminho_remoto.lstrip("/")
        return f"{base}/root:/{caminho_remoto}"
    return f"{base}/root"


def formatar_tamanho(bytes_: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if bytes_ < 1024:
            return f"{bytes_:.1f} {unit}"
        bytes_ /= 1024
    return f"{bytes_:.1f} TB"


def listar_itens(caminho_remoto: str = None) -> list:
    if caminho_remoto:
        url = f"{drive_url(caminho_remoto)}:/children"
    else:
        url = f"{drive_url()}/children"

    resp = _session.get(url, headers=headers())
    if resp.status_code != 200:
        handle_error(resp, f"Listar '{caminho_remoto or 'raiz'}'")

    return resp.json().get("value", [])


# ---------------------------------------------
# Listar
# ---------------------------------------------

def listar(caminho_remoto: str = None):
    itens = listar_itens(caminho_remoto)
    local = caminho_remoto or "(raiz)"

    if not itens:
        print("Pasta vazia.")
        return

    print(f"\nConteudo de: {local}\n")
    print(f"{'NOME':<45} {'TIPO':<10} {'TAMANHO':>10}  {'MODIFICADO'}")
    print("-" * 85)

    for item in itens:
        nome       = item.get("name", "")
        tamanho    = item.get("size", 0)
        modificado = item.get("lastModifiedDateTime", "")[:10]
        tipo       = "[pasta]  " if "folder" in item else "[arquivo]"
        print(f"{nome:<45} {tipo:<10} {formatar_tamanho(tamanho):>10}  {modificado}")

    print(f"\nTotal: {len(itens)} item(s)")


# ---------------------------------------------
# Upload
# ---------------------------------------------

def upload_simples(caminho_local: str, caminho_remoto: str):
    """
    Envia arquivos menores que UPLOAD_THRESHOLD via PUT simples.
    Usa ler_com_throttle para respeitar --speed mesmo em arquivos pequenos.
    """
    url     = f"{drive_url(caminho_remoto)}:/content"
    tamanho = os.path.getsize(caminho_local)

    hdrs = {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type":  "application/octet-stream",
    }

    with open(caminho_local, "rb") as f:
        conteudo = ler_com_throttle(f, tamanho)

    resp = _session.put(url, headers=hdrs, data=conteudo)
    if resp.status_code not in (200, 201):
        handle_error(resp, "Upload simples")

    log_ok(f"Arquivo enviado (simples): {caminho_remoto}")


def upload_session(caminho_local: str, caminho_remoto: str, tamanho_total: int):
    url_criar_sessao = f"{drive_url(caminho_remoto)}:/createUploadSession"
    body = {
        "item": {
            "@microsoft.graph.conflictBehavior": "replace",
            "name": os.path.basename(caminho_remoto),
        }
    }

    resp = _session.post(url_criar_sessao, headers=headers(), json=body)
    if resp.status_code != 200:
        handle_error(resp, "Criar upload session")

    upload_url = resp.json()["uploadUrl"]
    chunk_size = calcular_chunk_size()
    log_info(f"Upload session criada. Enviando em chunks de {formatar_tamanho(chunk_size)}...")
    log.debug(f"Upload session URL obtida para: {caminho_remoto}")

    offset = 0
    with open(caminho_local, "rb") as f:
        while offset < tamanho_total:
            chunk     = ler_com_throttle(f, chunk_size)
            chunk_len = len(chunk)
            fim       = offset + chunk_len - 1

            hdrs_chunk = {
                "Content-Length": str(chunk_len),
                "Content-Range":  f"bytes {offset}-{fim}/{tamanho_total}",
                "Content-Type":   "application/octet-stream",
            }

            resp_chunk = _session.put(upload_url, headers=hdrs_chunk, data=chunk)

            if resp_chunk.status_code not in (200, 201, 202):
                log_erro(f"Falha no chunk {offset}-{fim}: {resp_chunk.status_code} - {resp_chunk.text}")
                sys.exit(1)

            offset += chunk_len
            log.debug(f"Chunk enviado: {formatar_tamanho(offset)} / {formatar_tamanho(tamanho_total)} ({(offset / tamanho_total) * 100:.1f}%)")

    log_ok(f"Arquivo enviado (session): {caminho_remoto}")


def upload(caminho_local: str, caminho_remoto: str):
    if not os.path.isfile(caminho_local):
        log_erro(f"Arquivo local nao encontrado: {caminho_local}")
        sys.exit(1)

    nome_arquivo = os.path.basename(caminho_local)
    if not os.path.splitext(caminho_remoto)[1]:
        caminho_remoto = caminho_remoto.rstrip("/") + "/" + nome_arquivo
        log_info(f"Destino resolvido para: {caminho_remoto}")

    tamanho = os.path.getsize(caminho_local)
    log_info(f"Upload: {caminho_local} ({formatar_tamanho(tamanho)}) -> {caminho_remoto}")

    if tamanho >= UPLOAD_THRESHOLD:
        upload_session(caminho_local, caminho_remoto, tamanho)
    else:
        upload_simples(caminho_local, caminho_remoto)


# ---------------------------------------------
# Download
# ---------------------------------------------

def download_arquivo(caminho_remoto: str, caminho_local: str, throttle_estado: dict = None):
    """
    Baixa um unico arquivo do OneDrive para o caminho local.

    throttle_estado: dict compartilhado {'bytes': int, 'inicio': float} para
    manter controle de velocidade continuo em downloads recursivos de pastas.
    Quando fornecido, o contador de bytes e tempo e compartilhado entre todos
    os arquivos da sessao, garantindo que o throttle seja efetivo mesmo para
    muitos arquivos pequenos.
    Quando None (download avulso), cria seu proprio estado local.
    """
    url_meta  = f"{drive_url(caminho_remoto)}"
    resp_meta = _session.get(url_meta, headers=headers())
    if resp_meta.status_code != 200:
        handle_error(resp_meta, "Obter metadados para download")

    meta = resp_meta.json()

    if "folder" in meta:
        log_erro(f"'{caminho_remoto}' e uma pasta. Use --download apontando para a pasta.")
        sys.exit(1)

    tamanho_total = meta.get("size", 0)
    download_url  = meta.get("@microsoft.graph.downloadUrl")

    if not download_url:
        log_erro(f"Nao foi possivel obter a URL de download para: {caminho_remoto}")
        sys.exit(1)

    if os.path.isdir(caminho_local):
        nome_arquivo  = meta.get("name", os.path.basename(caminho_remoto))
        caminho_local = os.path.join(caminho_local, nome_arquivo)

    log_info(f"Baixando : {caminho_remoto} ({formatar_tamanho(tamanho_total)})")
    log_info(f"Destino  : {caminho_local}")

    # Throttle global compartilhado (pasta recursiva) ou local (arquivo avulso)
    estado_local = throttle_estado is None
    if estado_local:
        throttle_estado = {"bytes": 0, "inicio": time.time()}

    SUB_CHUNK = 256 * 1024  # 256 KB

    with _session.get(download_url, stream=True) as r:
        if r.status_code != 200:
            log_erro(f"Falha no download: {r.status_code}")
            sys.exit(1)

        with open(caminho_local, "wb") as f:
            for chunk in r.iter_content(chunk_size=SUB_CHUNK):
                if chunk:
                    f.write(chunk)
                    throttle_estado["bytes"] += len(chunk)
                    aplicar_throttle(throttle_estado["bytes"], throttle_estado["inicio"])

    log_ok(f"Download concluido: {caminho_local} ({formatar_tamanho(tamanho_total)})")


def download_pasta_recursivo(caminho_remoto: str, destino_local: str, nivel: int = 0,
                              throttle_estado: dict = None):
    """
    Download recursivo de pasta. Propaga throttle_estado para manter controle
    de velocidade continuo ao longo de toda a sessao de download.
    """
    itens    = listar_itens(caminho_remoto)
    arquivos = [i for i in itens if "folder" not in i]
    pastas   = [i for i in itens if "folder" in i]
    indent   = "  " * nivel

    os.makedirs(destino_local, exist_ok=True)

    if not itens:
        log_info(f"{indent}Pasta vazia: {caminho_remoto}")
        return

    if arquivos:
        log_info(f"{indent}{len(arquivos)} arquivo(s) em: {caminho_remoto}")
        for item in arquivos:
            nome        = item["name"]
            remoto_item = f"{caminho_remoto.rstrip('/')}/{nome}"
            log_info(f"\n{indent}v {nome}")
            download_arquivo(remoto_item, destino_local, throttle_estado=throttle_estado)

    if pastas:
        log_info(f"\n{indent}{len(pastas)} subpasta(s) em: {caminho_remoto}")
        for pasta in pastas:
            nome_pasta      = pasta["name"]
            remoto_subpasta = f"{caminho_remoto.rstrip('/')}/{nome_pasta}"
            local_subpasta  = os.path.join(destino_local, nome_pasta)
            log_info(f"\n{indent}+-- Entrando em: {nome_pasta}/")
            download_pasta_recursivo(remoto_subpasta, local_subpasta, nivel + 1,
                                     throttle_estado=throttle_estado)


def download(caminho_remoto: str, destino_local: str):
    url_meta  = f"{drive_url(caminho_remoto)}"
    resp_meta = _session.get(url_meta, headers=headers())
    if resp_meta.status_code != 200:
        handle_error(resp_meta, "Download")

    meta = resp_meta.json()

    if "folder" in meta:
        log_info(f"Iniciando download recursivo de: {caminho_remoto}")
        log_info(f"Destino local: {destino_local}\n")
        # Cria um estado de throttle global compartilhado para toda a pasta
        throttle_estado = {"bytes": 0, "inicio": time.time()} if _speed_limit_mbs > 0 else None
        download_pasta_recursivo(caminho_remoto, destino_local, throttle_estado=throttle_estado)
        log_ok(f"Download recursivo concluido: {destino_local}")
    else:
        download_arquivo(caminho_remoto, destino_local)


# ---------------------------------------------
# Delete
# ---------------------------------------------

def deletar(caminho_remoto: str):
    url  = f"{drive_url(caminho_remoto)}"
    resp = _session.delete(url, headers=headers())

    if resp.status_code == 204:
        log_ok(f"Item deletado: {caminho_remoto}")
    else:
        handle_error(resp, "Deletar")


def deletar_conteudo(caminho_remoto: str):
    itens = listar_itens(caminho_remoto)

    if not itens:
        log_info(f"Pasta ja esta vazia: {caminho_remoto}")
        return

    log_info(f"{len(itens)} item(s) encontrado(s) em: {caminho_remoto}")
    erros = 0

    for idx, item in enumerate(itens, start=1):
        nome        = item["name"]
        remoto_item = f"{caminho_remoto.rstrip('/')}/{nome}"
        tipo        = "pasta" if "folder" in item else "arquivo"

        resp = _session.delete(f"{drive_url(remoto_item)}", headers=headers())

        if resp.status_code == 204:
            log_ok(f"[{idx}/{len(itens)}] {tipo} deletado: {nome}")
        else:
            log_erro(f"[{idx}/{len(itens)}] Falha ao deletar {tipo}: {nome} - HTTP {resp.status_code}")
            erros += 1

    if erros == 0:
        log_ok(f"Todo o conteudo de '{caminho_remoto}' removido. Pasta mantida.")
    else:
        log_aviso(f"Concluido com {erros} erro(s). Pasta '{caminho_remoto}' mantida.")


# ---------------------------------------------
# Sync - mapeamento
# ---------------------------------------------

def mapear_remoto_recursivo_seguro(caminho_remoto: str) -> dict:
    """
    Versao segura do mapeamento remoto: se a pasta nao existir (404),
    retorna dicionario vazio sem exibir erro — ela sera criada no upload.
    Passa caminho_remoto como base_remoto para garantir caminhos relativos corretos.
    """
    url  = f"{drive_url(caminho_remoto)}:/children"
    resp = _session.get(url, headers=headers())

    if resp.status_code == 404:
        log_info(f"Pasta remota ainda nao existe, sera criada no primeiro upload: {caminho_remoto}")
        return {}
    elif resp.status_code != 200:
        handle_error(resp, f"Mapear remoto '{caminho_remoto}'")

    resultado = {}
    for item in resp.json().get("value", []):
        nome         = item["name"]
        caminho_item = f"{caminho_remoto.rstrip('/')}/{nome}"
        if "folder" in item:
            sub = mapear_remoto_recursivo(caminho_item, caminho_remoto)
            resultado.update(sub)
        else:
            resultado[nome] = item.get("size", 0)

    return resultado


def mapear_remoto_recursivo(caminho_remoto: str, base_remoto: str) -> dict:
    resultado = {}
    itens     = listar_itens(caminho_remoto)

    for item in itens:
        nome         = item["name"]
        caminho_item = f"{caminho_remoto.rstrip('/')}/{nome}"

        if "folder" in item:
            sub = mapear_remoto_recursivo(caminho_item, base_remoto)
            resultado.update(sub)
        else:
            relativo = caminho_item[len(base_remoto):].lstrip("/")
            resultado[relativo] = item.get("size", 0)

    return resultado


def mapear_local_recursivo(diretorio_local: str) -> dict:
    resultado = {}

    for raiz, _, arquivos in os.walk(diretorio_local):
        for nome in arquivos:
            caminho_abs = os.path.join(raiz, nome)
            relativo    = os.path.relpath(caminho_abs, diretorio_local).replace("\\", "/")
            resultado[relativo] = os.path.getsize(caminho_abs)

    return resultado


# ---------------------------------------------
# Watchdog - deteccao de arquivos em escrita
# ---------------------------------------------

class _ModificacaoHandler(FileSystemEventHandler):
    """
    Handler do watchdog que registra via inotify o timestamp da ultima
    modificacao de cada arquivo monitorado.
    Usado por aguardar_arquivo_estavel() para deteccao em tempo real.
    """
    def __init__(self):
        self._ultima_modificacao: dict = {}
        self._lock = threading.Lock()

    def on_modified(self, event):
        if not event.is_directory:
            with self._lock:
                self._ultima_modificacao[os.path.abspath(event.src_path)] = time.time()

    def on_created(self, event):
        self.on_modified(event)

    def ultima_modificacao(self, caminho: str) -> float:
        with self._lock:
            return self._ultima_modificacao.get(os.path.abspath(caminho), 0.0)


# Observer global do watchdog (iniciado uma vez por execucao de sync)
_observer: Observer = None
_handler:  _ModificacaoHandler = None


def iniciar_watchdog(diretorio: str):
    """Inicia o observer do watchdog monitorando diretorio recursivamente."""
    global _observer, _handler
    _handler  = _ModificacaoHandler()
    _observer = Observer()
    _observer.schedule(_handler, diretorio, recursive=True)
    _observer.start()
    log.debug(f"Watchdog iniciado em: {diretorio}")


def parar_watchdog():
    """Para o observer do watchdog se estiver ativo."""
    global _observer
    if _observer and _observer.is_alive():
        _observer.stop()
        _observer.join()
        log.debug("Watchdog encerrado.")


def aguardar_arquivo_estavel(caminho: str, janela: float = 5.0, intervalo: float = 1.0) -> bool:
    """
    Aguarda ate que o arquivo nao tenha sido modificado por pelo menos
    'janela' segundos consecutivos, verificando a cada 'intervalo' segundos.

    Combina duas fontes de informacao:
      1. Watchdog (eventos inotify em tempo real via _ModificacaoHandler)
      2. mtime do filesystem (fallback e verificacao extra de integridade)

    Retorna True quando o arquivo esta estavel e seguro para upload.
    Retorna False se o arquivo desaparecer durante a espera.

    Parametros:
      janela   : segundos sem modificacao para considerar estavel (padrao: 5s)
      intervalo: frequencia de verificacao em segundos (padrao: 1s)
    """
    caminho_abs = os.path.abspath(caminho)

    while True:
        if not os.path.exists(caminho_abs):
            return False

        agora        = time.time()
        mtime        = os.path.getmtime(caminho_abs)
        wtime        = _handler.ultima_modificacao(caminho_abs) if _handler else 0.0
        ultima_modif = max(mtime, wtime)

        if agora - ultima_modif >= janela:
            # Confirmacao final: tamanho identico apos mais um intervalo
            tam_antes = os.path.getsize(caminho_abs)
            time.sleep(intervalo)
            if not os.path.exists(caminho_abs):
                return False
            tam_depois = os.path.getsize(caminho_abs)
            if tam_antes == tam_depois:
                return True
            # Tamanho mudou: resetar espera
        else:
            restante = janela - (agora - ultima_modif)
            log.debug(
                f"Aguardando estabilidade: {os.path.basename(caminho_abs)} "
                f"(ultima modif ha {agora - ultima_modif:.1f}s, aguardando {restante:.1f}s mais)"
            )
            time.sleep(intervalo)


# ---------------------------------------------
# Sync
# ---------------------------------------------

def sync(diretorio_local: str, caminho_remoto: str, deletar_remotos: bool = False):
    if not os.path.isdir(diretorio_local):
        log_erro(f"Diretorio local nao encontrado: {diretorio_local}")
        sys.exit(1)

    log_secao("INICIO DO SYNC")
    log_info(f"Local  : {diretorio_local}")
    log_info(f"Remoto : {caminho_remoto}")
    log_info(f"Modo   : {'espelho (--mirror ativo)' if deletar_remotos else 'incremental (sem --mirror)'}")

    # Inicia o watchdog antes do mapeamento para capturar eventos durante o sync
    iniciar_watchdog(diretorio_local)

    log_info("Mapeando arquivos locais...")
    mapa_local = mapear_local_recursivo(diretorio_local)

    log_info("Mapeando arquivos remotos...")
    mapa_remoto = mapear_remoto_recursivo_seguro(caminho_remoto)

    log_info(f"Arquivos locais : {len(mapa_local)}")
    log_info(f"Arquivos remotos: {len(mapa_remoto)}")

    # Classificar
    a_enviar  = []
    identicos = []
    a_deletar = []

    for rel, tam_local in mapa_local.items():
        if rel not in mapa_remoto:
            a_enviar.append((rel, "novo"))
        elif mapa_remoto[rel] != tam_local:
            a_enviar.append((rel, "atualizado"))
        else:
            identicos.append(rel)

    if deletar_remotos:
        for rel in mapa_remoto:
            if rel not in mapa_local:
                a_deletar.append(rel)

    log_info(f"{len(identicos)} arquivo(s) identico(s) - ignorados")
    log_info(f"{len(a_enviar)} arquivo(s) a enviar")
    if deletar_remotos:
        log_info(f"{len(a_deletar)} arquivo(s) a deletar do remoto")

    if not a_enviar and not a_deletar:
        log_ok("Tudo sincronizado. Nenhuma acao necessaria.")
        parar_watchdog()
        return

    ignorados_instavel = []

    try:
        # Uploads
        if a_enviar:
            log_secao(f"UPLOADS ({len(a_enviar)} arquivo(s))")
            for idx, (rel, motivo) in enumerate(a_enviar, start=1):
                local_abs      = os.path.join(diretorio_local, rel.replace("/", os.sep))
                remoto_destino = f"{caminho_remoto.rstrip('/')}/{rel}"

                log_info(f"\n[{idx}/{len(a_enviar)}] [{motivo.upper()}] {rel}")

                # Aguarda o arquivo parar de ser modificado antes de enviar
                log.debug(f"Verificando estabilidade: {rel}")
                if not aguardar_arquivo_estavel(local_abs):
                    log_aviso(f"Arquivo desapareceu durante a espera, ignorando: {rel}")
                    ignorados_instavel.append(rel)
                    continue

                tamanho = os.path.getsize(local_abs)
                log_info(f"  Tamanho : {formatar_tamanho(tamanho)} - arquivo estavel, iniciando upload.")

                if tamanho >= UPLOAD_THRESHOLD:
                    upload_session(local_abs, remoto_destino, tamanho)
                else:
                    upload_simples(local_abs, remoto_destino)

        # Delecoes remotas
        if a_deletar:
            log_secao(f"DELECOES REMOTAS ({len(a_deletar)} arquivo(s))")
            for idx, rel in enumerate(a_deletar, start=1):
                remoto_item = f"{caminho_remoto.rstrip('/')}/{rel}"
                log_info(f"[{idx}/{len(a_deletar)}] Deletando: {rel}")
                deletar(remoto_item)

    finally:
        parar_watchdog()

    # Resumo final
    log_secao("RESUMO SYNC")
    log_info(f"  Enviados        : {len(a_enviar) - len(ignorados_instavel)}")
    log_info(f"  Deletados       : {len(a_deletar)}")
    log_info(f"  Ignorados       : {len(identicos)}")
    if ignorados_instavel:
        log_aviso(f"  Instaveis (skip): {len(ignorados_instavel)} - execute novamente apos a geracao concluir.")
    log_ok("Sync concluido.")


# ---------------------------------------------
# CLI
# ---------------------------------------------

def gerar_env():
    """Gera um arquivo .env de exemplo no diretorio atual."""
    destino = os.path.join(os.getcwd(), ".env")

    if os.path.exists(destino):
        resposta = input(f"[AVISO] O arquivo '{destino}' ja existe. Sobrescrever? [s/N] ").strip().lower()
        if resposta != "s":
            print("[INFO] Operacao cancelada. Nenhum arquivo foi alterado.")
            return

    linhas = [
        "# -------------------------------------------------------",
        "# Credenciais do aplicativo registrado no Azure AD",
        "# -------------------------------------------------------",
        "",
        "# ID do aplicativo (cliente) - Azure AD > App registrations > seu app > Application (client) ID",
        "CLIENT_ID=seu-client-id-aqui",
        "",
        "# Valor do segredo do cliente - Azure AD > App registrations > seu app > Certificates & secrets",
        "CLIENT_SECRET=seu-client-secret-aqui",
        "",
        "# ID do diretorio (locatario) - Azure AD > App registrations > seu app > Directory (tenant) ID",
        "TENANT_ID=seu-tenant-id-aqui",
        "",
        "# E-mail ou ID do usuario cujo OneDrive sera acessado",
        "USER_ID=usuario@suaempresa.com",
        "",
        "# -------------------------------------------------------",
        "# Configuracoes opcionais",
        "# -------------------------------------------------------",
        "",
        "# Diretorio onde os arquivos de log serao salvos (padrao: /var/log/getonefiles)",
        "# LOG_DIR=/var/log/getonefiles",
        "",
        "# Limite de velocidade de upload/download em MB/s (0 = sem limite)",
        "# SPEED_LIMIT=5",
        "",
        "# Permitir conexoes via IPv6 (padrao: somente IPv4)",
        "# IPV6=false",
    ]

    with open(destino, "w") as f:
        f.write("\n".join(linhas) + "\n")

    print(f"[OK] Arquivo .env criado em: {destino}")
    print("[INFO] Edite o arquivo e preencha com suas credenciais antes de usar o script.")


def validar_env():
    faltando = [v for v in ("CLIENT_ID", "CLIENT_SECRET", "TENANT_ID", "USER_ID") if not os.getenv(v)]
    if faltando:
        print(f"[ERRO] Variaveis ausentes no .env: {', '.join(faltando)}")
        sys.exit(1)


def main():
    sep    = "=" * 62
    epilog = "\n".join([
        "",
        sep,
        " CONFIGURACAO INICIAL - AZURE AD (Microsoft Entra)",
        sep,
        "",
        "Fase 1: Criacao do Aplicativo (App Registration)",
        "  1. Acesse https://entra.microsoft.com com uma conta de administrador global.",
        "  2. No menu lateral, va em Registros de aplicativo > Novo registro.",
        "  3. Em Nome, defina como getonefiles (ou o nome do seu projeto).",
        "  4. Em Tipos de conta com suporte, selecione Somente locatario unico (Single tenant).",
        "  5. Ignore a configuracao de URI de redirecionamento e clique em Registrar.",
        "",
        "Fase 2: Configuracao de Permissoes (Acesso em Background)",
        "  1. No menu lateral do novo aplicativo, acesse Permissoes de APIs.",
        "  2. Clique em Adicionar uma permissao > Microsoft Graph >",
        "     Permissoes de aplicativo (NAO escolha o tipo Delegado).",
        "  3. Encontre e marque as seguintes permissoes:",
        "       - User.Read.All",
        "       - Files.Read.All",
        "       - Files.ReadWrite.All",
        "  4. Clique em Adicionar permissoes no rodape da pagina.",
        "  5. ACAO OBRIGATORIA: Clique em Conceder consentimento do administrador",
        "     para [Sua Organizacao] e confirme. Verifique se um check verde",
        "     apareceu na coluna de status de todas as permissoes.",
        "",
        "Fase 3: Geracao e Coleta de Credenciais",
        "  1. Va em Certificados e segredos > Novo segredo do cliente.",
        "  2. Insira uma Descricao e defina o tempo de expiracao. Clique em Adicionar.",
        "  3. ATENCAO: Copie imediatamente a string da coluna Valor. Este e o seu",
        "     CLIENT_SECRET. Ele ficara permanentemente oculto ao sair desta tela.",
        "     (Ignore a coluna ID Secreto, ela nao tem utilidade para a API.)",
        "  4. Va em Visao Geral (Overview) do aplicativo e copie:",
        "       - ID do aplicativo (cliente)  ->  CLIENT_ID",
        "       - ID do diretorio (locatario) ->  TENANT_ID",
        "",
        "Use o comando abaixo para gerar o arquivo .env pre-configurado:",
        "  python3 getonefiles.py --init",
        sep,
    ])

    parser = argparse.ArgumentParser(
        description="Gerencia arquivos no OneDrive via Microsoft Graph API",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=epilog,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        nargs="?",
        const="",
        metavar="CAMINHO_REMOTO",
        help="Lista arquivos. Sem argumento, lista a raiz.\nEx: --list \"Documentos/Backup\"",
    )
    group.add_argument(
        "--upload",
        nargs=2,
        metavar=("ARQUIVO_LOCAL", "CAMINHO_REMOTO"),
        help="Faz upload de um arquivo.\nEx: --upload /tmp/log.txt \"Documentos/HS-STG-02/\"",
    )
    group.add_argument(
        "--download",
        nargs=2,
        metavar=("CAMINHO_REMOTO", "DESTINO_LOCAL"),
        help=(
            "Baixa um arquivo ou pasta inteira (recursivo).\n"
            "Ex (arquivo): --download \"Documentos/HS-STG-02/Full/arquivo.tar\" /mnt/Local/\n"
            "Ex (pasta)  : --download \"Documentos/HS-STG-02/Full\" /mnt/Local/"
        ),
    )
    group.add_argument(
        "--delete",
        metavar="CAMINHO_REMOTO",
        help=(
            "Deleta um arquivo ou uma pasta inteira (com todo o conteudo).\n"
            "Ex: --delete \"Documentos/HS-STG-02/Full/arquivo.txt\"\n"
            "Ex: --delete \"Documentos/HS-STG-02/Full\""
        ),
    )
    group.add_argument(
        "--delete-contents",
        metavar="CAMINHO_REMOTO",
        help=(
            "Deleta apenas o conteudo de uma pasta, mantendo a pasta em si.\n"
            "Ex: --delete-contents \"Documentos/HS-STG-02/Full\""
        ),
    )
    group.add_argument(
        "--sync",
        nargs=2,
        metavar=("DIR_LOCAL", "CAMINHO_REMOTO"),
        help=(
            "Sincroniza um diretorio local com o OneDrive (local -> remoto).\n"
            "Envia arquivos novos ou com tamanho diferente. Ignora identicos.\n"
            "Use --mirror para remover do OneDrive arquivos ausentes localmente.\n"
            "Ex: --sync /mnt/Local/Backup \"Documentos/HS-STG-02\"\n"
            "Ex: --sync /mnt/Local/Backup \"Documentos/HS-STG-02\" --mirror"
        ),
    )
    group.add_argument(
        "--init",
        action="store_true",
        help="Gera um arquivo .env de exemplo no diretorio atual.",
    )

    # Flags opcionais do --sync
    parser.add_argument(
        "--mirror",
        dest="sync_delete",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )

    # Limite de velocidade: CLI sobrescreve o .env
    parser.add_argument(
        "--speed",
        dest="speed",
        metavar="VELOCIDADE",
        default=None,
        help=(
            "Limita a velocidade de upload/download.\n"
            "Formato: --speed 5mb ou --speed 5\n"
            "Padrao: sem limite (ou SPEED_LIMIT no .env)"
        ),
    )

    # Permitir IPv6 (desativa o padrao IPv4-only)
    parser.add_argument(
        "--ipv6",
        dest="force_ipv6",
        action="store_true",
        default=False,
        help="Permite conexoes via IPv6 (por padrao somente IPv4 e usado).",
    )

    # Diretorio de log: CLI sobrescreve o .env
    parser.add_argument(
        "--log-dir",
        dest="log_dir",
        metavar="DIR",
        default=DEFAULT_LOG_DIR,
        help=(
            "Diretorio para salvar os arquivos de log.\n"
            f"Padrao: {DEFAULT_LOG_DIR} (ou LOG_DIR no .env)\n"
            "Ex: --log-dir /tmp/logs"
        ),
    )

    args = parser.parse_args()

    # --init nao precisa de credenciais nem de log
    if args.init:
        gerar_env()
        return

    # Configura limite de velocidade (CLI tem prioridade sobre .env)
    global _speed_limit_mbs, _force_ipv6, _session
    if args.speed is not None:
        raw = args.speed.lower().replace("mb", "").strip()
        try:
            _speed_limit_mbs = float(raw)
        except ValueError:
            print(f"[ERRO] Valor invalido para --speed: {args.speed}. Use ex: --speed 5mb")
            sys.exit(1)
    else:
        _speed_limit_mbs = DEFAULT_SPEED_MB

    # Configura stack de rede e reinicializa a session HTTP global
    _force_ipv6 = args.force_ipv6 or os.getenv("IPV6", "").lower() in ("1", "true", "yes")
    _session    = criar_session()
    if _force_ipv6:
        print("[INFO] Modo IPv6 ativado: conexoes podem usar AF_INET6.")
    else:
        print("[INFO] Modo IPv4 (padrao): conexoes restritas a AF_INET.")

    # --list nao gera log: executa e retorna imediatamente
    if args.list is not None:
        listar(args.list or None)
        return

    # Para todas as outras operacoes, valida .env e inicializa log
    validar_env()
    setup_logging(args.log_dir)

    if _speed_limit_mbs > 0:
        log_info(f"Limite de velocidade: {_speed_limit_mbs:.1f} MB/s")

    if args.upload:
        log.debug(f"Operacao: upload | local: {args.upload[0]} | remoto: {args.upload[1]}")
        upload(args.upload[0], args.upload[1])
    elif args.download:
        log.debug(f"Operacao: download | remoto: {args.download[0]} | local: {args.download[1]}")
        download(args.download[0], args.download[1])
    elif args.delete:
        log.debug(f"Operacao: delete | caminho: {args.delete}")
        deletar(args.delete)
    elif args.delete_contents:
        log.debug(f"Operacao: delete-contents | caminho: {args.delete_contents}")
        deletar_conteudo(args.delete_contents)
    elif args.sync:
        log.debug(f"Operacao: sync | local: {args.sync[0]} | remoto: {args.sync[1]} | mirror: {args.sync_delete}")
        sync(args.sync[0], args.sync[1], deletar_remotos=args.sync_delete)

    log.info("=== Fim da execucao ===")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[AVISO] Execucao interrompida pelo usuario.")
        log.warning("Execucao interrompida pelo usuario (Ctrl+C).")
        parar_watchdog()
        sys.exit(0)