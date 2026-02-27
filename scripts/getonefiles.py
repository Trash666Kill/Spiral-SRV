#!/usr/bin/env python3
"""
OneDrive CLI via Microsoft Graph API
Uso:
  python onedrive.py --list
  python onedrive.py --list "Documentos/HS-STG-02"
  python onedrive.py --upload /caminho/local/arquivo.txt "Documentos/HS-STG-02/arquivo.txt"
  python onedrive.py --upload /caminho/local/arquivo.txt "Documentos/HS-STG-02/"
  python onedrive.py --download "Documentos/HS-STG-02/Full/arquivo.tar" /mnt/Local/destino/
  python onedrive.py --download "Documentos/HS-STG-02/Full" /mnt/Local/destino/
  python onedrive.py --delete "Documentos/HS-STG-02/Full/arquivo.txt"
  python onedrive.py --delete "Documentos/HS-STG-02/Full"
  python onedrive.py --delete-contents "Documentos/HS-STG-02/Full"
  python onedrive.py --sync /mnt/Local/Backup "Documentos/HS-STG-02"
  python onedrive.py --sync /mnt/Local/Backup "Documentos/HS-STG-02" --delete
  python onedrive.py --sync /mnt/Local/Backup "Documentos/HS-STG-02" --log-dir /tmp/logs
"""

import os
import sys
import time
import logging
import argparse
from datetime import datetime
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------
# Configuracao
# ---------------------------------------------

CLIENT_ID        = os.getenv("CLIENT_ID")
CLIENT_SECRET    = os.getenv("CLIENT_SECRET")
TENANT_ID        = os.getenv("TENANT_ID")
USER_ID          = os.getenv("USER_ID")
DEFAULT_LOG_DIR  = os.getenv("LOG_DIR", "/var/log/getonefiles")

# Limite de velocidade em MB/s (0 = sem limite)
# Pode ser definido no .env como SPEED_LIMIT=5mb ou SPEED_LIMIT=5
_raw_speed = os.getenv("SPEED_LIMIT", "0").lower().replace("mb", "").strip()
DEFAULT_SPEED_MB = float(_raw_speed) if _raw_speed else 0.0

GRAPH_BASE       = "https://graph.microsoft.com/v1.0"
TOKEN_URL        = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
CHUNK_SIZE       = 10 * 1024 * 1024   # 10 MB por chunk
UPLOAD_THRESHOLD = 4  * 1024 * 1024   # arquivos >= 4 MB usam upload session

# Logger global (configurado em setup_logging)
log = logging.getLogger("getonefiles")

# Limite de velocidade ativo na execucao (MB/s); 0 = sem limite
_speed_limit_mbs: float = 0.0


def aplicar_throttle(bytes_transferidos: int, tempo_inicio: float):
    """
    Pausa o tempo necessario para respeitar o limite de velocidade.
    bytes_transferidos: total de bytes ja transferidos nesta sessao
    tempo_inicio: timestamp do inicio da transferencia
    """
    if _speed_limit_mbs <= 0:
        return

    limite_bytes_s = _speed_limit_mbs * 1024 * 1024
    tempo_esperado = bytes_transferidos / limite_bytes_s
    tempo_decorrido = time.time() - tempo_inicio
    espera = tempo_esperado - tempo_decorrido

    if espera > 0:
        time.sleep(espera)


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

    timestamp  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    log_file   = os.path.join(log_dir, f"getonefiles_{timestamp}.log")

    log.setLevel(logging.DEBUG)

    fmt     = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fmt_con = logging.Formatter("%(message)s")  # terminal: sem timestamp (ja aparece no print)

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

    resp = requests.post(TOKEN_URL, data=payload)
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

    resp = requests.get(url, headers=headers())
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
        log_info("Pasta vazia.")
        return

    log_info(f"\nConteudo de: {local}\n")
    log_info(f"{'NOME':<45} {'TIPO':<10} {'TAMANHO':>10}  {'MODIFICADO'}")
    log_info("-" * 85)

    for item in itens:
        nome       = item.get("name", "")
        tamanho    = item.get("size", 0)
        modificado = item.get("lastModifiedDateTime", "")[:10]
        tipo       = "[pasta]  " if "folder" in item else "[arquivo]"
        log_info(f"{nome:<45} {tipo:<10} {formatar_tamanho(tamanho):>10}  {modificado}")

    log_info(f"\nTotal: {len(itens)} item(s)")
    log.debug(f"Listagem de '{local}' concluida: {len(itens)} item(s).")


# ---------------------------------------------
# Upload
# ---------------------------------------------

def upload_simples(caminho_local: str, caminho_remoto: str):
    url = f"{drive_url(caminho_remoto)}:/content"

    with open(caminho_local, "rb") as f:
        conteudo = f.read()

    hdrs = {
        "Authorization": f"Bearer {get_token()}",
        "Content-Type":  "application/octet-stream",
    }

    resp = requests.put(url, headers=hdrs, data=conteudo)
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

    resp = requests.post(url_criar_sessao, headers=headers(), json=body)
    if resp.status_code != 200:
        handle_error(resp, "Criar upload session")

    upload_url = resp.json()["uploadUrl"]
    log_info(f"[INFO] Upload session criada. Enviando em chunks de {CHUNK_SIZE // (1024 * 1024)} MB...")
    log.debug(f"Upload session URL obtida para: {caminho_remoto}")

    offset      = 0
    tempo_inicio = time.time()
    with open(caminho_local, "rb") as f:
        while offset < tamanho_total:
            chunk     = f.read(CHUNK_SIZE)
            chunk_len = len(chunk)
            fim       = offset + chunk_len - 1

            hdrs_chunk = {
                "Content-Length": str(chunk_len),
                "Content-Range":  f"bytes {offset}-{fim}/{tamanho_total}",
                "Content-Type":   "application/octet-stream",
            }

            resp_chunk = requests.put(upload_url, headers=hdrs_chunk, data=chunk)

            if resp_chunk.status_code not in (200, 201, 202):
                log_erro(f"Falha no chunk {offset}-{fim}: {resp_chunk.status_code} - {resp_chunk.text}")
                sys.exit(1)

            offset += chunk_len
            aplicar_throttle(offset, tempo_inicio)

            progresso = (offset / tamanho_total) * 100
            print(f"  -> {formatar_tamanho(offset)} / {formatar_tamanho(tamanho_total)} ({progresso:.1f}%)")
            log.debug(f"Chunk enviado: {formatar_tamanho(offset)} / {formatar_tamanho(tamanho_total)} ({progresso:.1f}%)")

    log_ok(f"Arquivo enviado (session): {caminho_remoto}")


def upload(caminho_local: str, caminho_remoto: str):
    if not os.path.isfile(caminho_local):
        log_erro(f"Arquivo local nao encontrado: {caminho_local}")
        sys.exit(1)

    nome_arquivo = os.path.basename(caminho_local)
    if not os.path.splitext(caminho_remoto)[1]:
        caminho_remoto = caminho_remoto.rstrip("/") + "/" + nome_arquivo
        log_info(f"[INFO] Destino resolvido para: {caminho_remoto}")

    tamanho = os.path.getsize(caminho_local)
    log_info(f"[INFO] Upload: {caminho_local} ({formatar_tamanho(tamanho)}) -> {caminho_remoto}")

    if tamanho >= UPLOAD_THRESHOLD:
        upload_session(caminho_local, caminho_remoto, tamanho)
    else:
        upload_simples(caminho_local, caminho_remoto)


# ---------------------------------------------
# Download
# ---------------------------------------------

def download_arquivo(caminho_remoto: str, caminho_local: str):
    url_meta  = f"{drive_url(caminho_remoto)}"
    resp_meta = requests.get(url_meta, headers=headers())
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

    log_info(f"[INFO] Baixando : {caminho_remoto} ({formatar_tamanho(tamanho_total)})")
    log_info(f"[INFO] Destino  : {caminho_local}")

    with requests.get(download_url, stream=True) as r:
        if r.status_code != 200:
            log_erro(f"Falha no download: {r.status_code}")
            sys.exit(1)

        baixado      = 0
        tempo_inicio = time.time()
        with open(caminho_local, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    baixado += len(chunk)
                    aplicar_throttle(baixado, tempo_inicio)
                    if tamanho_total:
                        progresso = (baixado / tamanho_total) * 100
                        print(f"  -> {formatar_tamanho(baixado)} / {formatar_tamanho(tamanho_total)} ({progresso:.1f}%)")
                    else:
                        print(f"  -> {formatar_tamanho(baixado)}")

    log_ok(f"Download concluido: {caminho_local} ({formatar_tamanho(tamanho_total)})")


def download_pasta_recursivo(caminho_remoto: str, destino_local: str, nivel: int = 0):
    itens    = listar_itens(caminho_remoto)
    arquivos = [i for i in itens if "folder" not in i]
    pastas   = [i for i in itens if "folder" in i]
    indent   = "  " * nivel

    os.makedirs(destino_local, exist_ok=True)

    if not itens:
        log_info(f"{indent}[INFO] Pasta vazia: {caminho_remoto}")
        return

    if arquivos:
        log_info(f"{indent}[INFO] {len(arquivos)} arquivo(s) em: {caminho_remoto}")
        for item in arquivos:
            nome        = item["name"]
            remoto_item = f"{caminho_remoto.rstrip('/')}/{nome}"
            log_info(f"\n{indent}v {nome}")
            download_arquivo(remoto_item, destino_local)

    if pastas:
        log_info(f"\n{indent}[INFO] {len(pastas)} subpasta(s) em: {caminho_remoto}")
        for pasta in pastas:
            nome_pasta      = pasta["name"]
            remoto_subpasta = f"{caminho_remoto.rstrip('/')}/{nome_pasta}"
            local_subpasta  = os.path.join(destino_local, nome_pasta)
            log_info(f"\n{indent}+-- Entrando em: {nome_pasta}/")
            download_pasta_recursivo(remoto_subpasta, local_subpasta, nivel + 1)


def download(caminho_remoto: str, destino_local: str):
    url_meta  = f"{drive_url(caminho_remoto)}"
    resp_meta = requests.get(url_meta, headers=headers())
    if resp_meta.status_code != 200:
        handle_error(resp_meta, "Download")

    meta = resp_meta.json()

    if "folder" in meta:
        log_info(f"\n[INFO] Iniciando download recursivo de: {caminho_remoto}")
        log_info(f"[INFO] Destino local: {destino_local}\n")
        download_pasta_recursivo(caminho_remoto, destino_local)
        log_ok(f"Download recursivo concluido: {destino_local}")
    else:
        download_arquivo(caminho_remoto, destino_local)


# ---------------------------------------------
# Delete
# ---------------------------------------------

def deletar(caminho_remoto: str):
    url  = f"{drive_url(caminho_remoto)}"
    resp = requests.delete(url, headers=headers())

    if resp.status_code == 204:
        log_ok(f"Item deletado: {caminho_remoto}")
    else:
        handle_error(resp, "Deletar")


def deletar_conteudo(caminho_remoto: str):
    itens = listar_itens(caminho_remoto)

    if not itens:
        log_info(f"[INFO] Pasta ja esta vazia: {caminho_remoto}")
        return

    log_info(f"[INFO] {len(itens)} item(s) encontrado(s) em: {caminho_remoto}")
    erros = 0

    for idx, item in enumerate(itens, start=1):
        nome        = item["name"]
        remoto_item = f"{caminho_remoto.rstrip('/')}/{nome}"
        tipo        = "pasta" if "folder" in item else "arquivo"

        resp = requests.delete(f"{drive_url(remoto_item)}", headers=headers())

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
# Sync
# ---------------------------------------------

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


def sync(diretorio_local: str, caminho_remoto: str, deletar_remotos: bool = False):
    if not os.path.isdir(diretorio_local):
        log_erro(f"Diretorio local nao encontrado: {diretorio_local}")
        sys.exit(1)

    log_secao("INICIO DO SYNC")
    log_info(f"[SYNC] Local  : {diretorio_local}")
    log_info(f"[SYNC] Remoto : {caminho_remoto}")
    log_info(f"[SYNC] Modo   : {'espelho (--delete ativo)' if deletar_remotos else 'incremental (sem --delete)'}")

    log_info(f"\n[INFO] Mapeando arquivos locais...")
    mapa_local = mapear_local_recursivo(diretorio_local)

    log_info(f"[INFO] Mapeando arquivos remotos...")
    try:
        mapa_remoto = mapear_remoto_recursivo(caminho_remoto, caminho_remoto)
    except SystemExit:
        mapa_remoto = {}

    log_info(f"\n[INFO] Arquivos locais : {len(mapa_local)}")
    log_info(f"[INFO] Arquivos remotos: {len(mapa_remoto)}")

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

    log_info(f"\n[SYNC] {len(identicos)} arquivo(s) identico(s) - ignorados")
    log_info(f"[SYNC] {len(a_enviar)} arquivo(s) a enviar")
    if deletar_remotos:
        log_info(f"[SYNC] {len(a_deletar)} arquivo(s) a deletar do remoto")

    if not a_enviar and not a_deletar:
        log_ok("Tudo sincronizado. Nenhuma acao necessaria.")
        return

    # Uploads
    if a_enviar:
        log_secao(f"UPLOADS ({len(a_enviar)} arquivo(s))")
        for idx, (rel, motivo) in enumerate(a_enviar, start=1):
            local_abs      = os.path.join(diretorio_local, rel.replace("/", os.sep))
            remoto_destino = f"{caminho_remoto.rstrip('/')}/{rel}"
            tamanho        = os.path.getsize(local_abs)

            log_info(f"\n[{idx}/{len(a_enviar)}] [{motivo.upper()}] {rel} ({formatar_tamanho(tamanho)})")

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

    # Resumo final
    log_secao("RESUMO SYNC")
    log_info(f"  Enviados  : {len(a_enviar)}")
    log_info(f"  Deletados : {len(a_deletar)}")
    log_info(f"  Ignorados : {len(identicos)}")
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

    conteudo = """# -------------------------------------------------------
# Credenciais do aplicativo registrado no Azure AD
# -------------------------------------------------------

# ID do aplicativo (cliente) - Azure AD > App registrations > seu app > Application (client) ID
CLIENT_ID=seu-client-id-aqui

# Valor do segredo do cliente - Azure AD > App registrations > seu app > Certificates & secrets
CLIENT_SECRET=seu-client-secret-aqui

# ID do diretorio (locatario) - Azure AD > App registrations > seu app > Directory (tenant) ID
TENANT_ID=seu-tenant-id-aqui

# E-mail ou ID do usuario cujo OneDrive sera acessado
USER_ID=usuario@suaempresa.com

# -------------------------------------------------------
# Configuracoes opcionais
# -------------------------------------------------------

# Diretorio onde os arquivos de log serao salvos (padrao: /var/log/getonefiles)
# LOG_DIR=/var/log/getonefiles
"""

    with open(destino, "w") as f:
        f.write(conteudo)

    print(f"[OK] Arquivo .env criado em: {destino}")
    print("[INFO] Edite o arquivo e preencha com suas credenciais antes de usar o script.")


def validar_env():
    faltando = [v for v in ("CLIENT_ID", "CLIENT_SECRET", "TENANT_ID", "USER_ID") if not os.getenv(v)]
    if faltando:
        print(f"[ERRO] Variaveis ausentes no .env: {', '.join(faltando)}")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description="Gerencia arquivos no OneDrive via Microsoft Graph API",
        formatter_class=argparse.RawTextHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--list",
        nargs="?",
        const="",
        metavar="CAMINHO_REMOTO",
        help='Lista arquivos. Sem argumento, lista a raiz.\nEx: --list "Documentos/Backup"',
    )
    group.add_argument(
        "--upload",
        nargs=2,
        metavar=("ARQUIVO_LOCAL", "CAMINHO_REMOTO"),
        help='Faz upload de um arquivo.\nEx: --upload /tmp/log.txt "Documentos/HS-STG-02/"',
    )
    group.add_argument(
        "--download",
        nargs=2,
        metavar=("CAMINHO_REMOTO", "DESTINO_LOCAL"),
        help=(
            'Baixa um arquivo ou pasta inteira (recursivo).\n'
            'Ex (arquivo): --download "Documentos/HS-STG-02/Full/arquivo.tar" /mnt/Local/\n'
            'Ex (pasta)  : --download "Documentos/HS-STG-02/Full" /mnt/Local/'
        ),
    )
    group.add_argument(
        "--delete",
        metavar="CAMINHO_REMOTO",
        help=(
            'Deleta um arquivo ou uma pasta inteira (com todo o conteudo).\n'
            'Ex: --delete "Documentos/HS-STG-02/Full/arquivo.txt"\n'
            'Ex: --delete "Documentos/HS-STG-02/Full"'
        ),
    )
    group.add_argument(
        "--delete-contents",
        metavar="CAMINHO_REMOTO",
        help=(
            'Deleta apenas o conteudo de uma pasta, mantendo a pasta em si.\n'
            'Ex: --delete-contents "Documentos/HS-STG-02/Full"'
        ),
    )
    group.add_argument(
        "--sync",
        nargs=2,
        metavar=("DIR_LOCAL", "CAMINHO_REMOTO"),
        help=(
            'Sincroniza um diretorio local com o OneDrive (local -> remoto).\n'
            'Envia arquivos novos ou com tamanho diferente. Ignora identicos.\n'
            'Use --mirror para remover do OneDrive arquivos ausentes localmente.\n'
            'Ex: --sync /mnt/Local/Backup "Documentos/HS-STG-02"\n'
            'Ex: --sync /mnt/Local/Backup "Documentos/HS-STG-02" --mirror'
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

    # Diretorio de log: CLI sobrescreve o .env
    parser.add_argument(
        "--log-dir",
        dest="log_dir",
        metavar="DIR",
        default=DEFAULT_LOG_DIR,
        help=(
            f'Diretorio para salvar os arquivos de log.\n'
            f'Padrao: {DEFAULT_LOG_DIR} (ou LOG_DIR no .env)\n'
            f'Ex: --log-dir /tmp/logs'
        ),
    )

    args = parser.parse_args()

    # --init nao precisa de credenciais nem de log
    if args.init:
        gerar_env()
        return

    # Configura limite de velocidade (CLI tem prioridade sobre .env)
    global _speed_limit_mbs
    if args.speed is not None:
        raw = args.speed.lower().replace("mb", "").strip()
        try:
            _speed_limit_mbs = float(raw)
        except ValueError:
            print(f"[ERRO] Valor invalido para --speed: {args.speed}. Use ex: --speed 5mb")
            sys.exit(1)
    else:
        _speed_limit_mbs = DEFAULT_SPEED_MB

    if _speed_limit_mbs > 0:
        log_info(f"[INFO] Limite de velocidade: {_speed_limit_mbs:.1f} MB/s")

    # Para todas as outras operacoes, valida o .env
    validar_env()

    # Inicializa logging antes de qualquer operacao
    log_file = setup_logging(args.log_dir)

    if args.list is not None:
        log.debug(f"Operacao: list | caminho: {args.list or 'raiz'}")
        listar(args.list or None)
    elif args.upload:
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
        log.debug(f"Operacao: sync | local: {args.sync[0]} | remoto: {args.sync[1]} | delete: {args.sync_delete}")
        sync(args.sync[0], args.sync[1], deletar_remotos=args.sync_delete)

    log.info("=== Fim da execucao ===")


if __name__ == "__main__":
    main()