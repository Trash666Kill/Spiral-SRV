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
"""

import os
import sys
import time
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------
# Configuração
# ---------------------------------------------

CLIENT_ID        = os.getenv("CLIENT_ID")
CLIENT_SECRET    = os.getenv("CLIENT_SECRET")
TENANT_ID        = os.getenv("TENANT_ID")
USER_ID          = os.getenv("USER_ID")

GRAPH_BASE       = "https://graph.microsoft.com/v1.0"
TOKEN_URL        = f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token"
CHUNK_SIZE       = 10 * 1024 * 1024   # 10 MB por chunk
UPLOAD_THRESHOLD = 4  * 1024 * 1024   # arquivos >= 4 MB usam upload session

# ---------------------------------------------
# Gerenciamento de token
# ---------------------------------------------

_token_cache = {"access_token": None, "expires_at": 0}


def get_token() -> str:
    """Retorna um token valido, renovando automaticamente se necessario."""
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
        print(f"[ERRO] Falha ao obter token: {resp.status_code} - {resp.text}")
        sys.exit(1)

    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    _token_cache["expires_at"]   = time.time() + data["expires_in"]
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
    print(f"[ERRO] {acao}: {msg}")
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
    """Retorna a lista de itens de uma pasta (sem exibir nada)."""
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

    if not itens:
        print("Pasta vazia.")
        return

    local = caminho_remoto or "(raiz)"
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

    print(f"[OK] Arquivo enviado: {caminho_remoto}")


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
    print(f"[INFO] Upload session criada. Enviando em chunks de {CHUNK_SIZE // (1024 * 1024)} MB...")

    offset = 0
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
                print(f"[ERRO] Falha no chunk {offset}-{fim}: {resp_chunk.status_code} - {resp_chunk.text}")
                sys.exit(1)

            offset    += chunk_len
            progresso  = (offset / tamanho_total) * 100
            print(f"  -> {formatar_tamanho(offset)} / {formatar_tamanho(tamanho_total)} ({progresso:.1f}%)")

    print(f"[OK] Arquivo enviado: {caminho_remoto}")


def upload(caminho_local: str, caminho_remoto: str):
    if not os.path.isfile(caminho_local):
        print(f"[ERRO] Arquivo local nao encontrado: {caminho_local}")
        sys.exit(1)

    nome_arquivo = os.path.basename(caminho_local)
    if not os.path.splitext(caminho_remoto)[1]:
        caminho_remoto = caminho_remoto.rstrip("/") + "/" + nome_arquivo
        print(f"[INFO] Destino resolvido para: {caminho_remoto}")

    tamanho = os.path.getsize(caminho_local)
    print(f"[INFO] Arquivo: {caminho_local} ({formatar_tamanho(tamanho)})")

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
        print(f"[ERRO] '{caminho_remoto}' e uma pasta. Use --download apontando para a pasta.")
        sys.exit(1)

    tamanho_total = meta.get("size", 0)
    download_url  = meta.get("@microsoft.graph.downloadUrl")

    if not download_url:
        print(f"[ERRO] Nao foi possivel obter a URL de download para: {caminho_remoto}")
        sys.exit(1)

    if os.path.isdir(caminho_local):
        nome_arquivo  = meta.get("name", os.path.basename(caminho_remoto))
        caminho_local = os.path.join(caminho_local, nome_arquivo)

    print(f"[INFO] Baixando : {caminho_remoto} ({formatar_tamanho(tamanho_total)})")
    print(f"[INFO] Destino  : {caminho_local}")

    with requests.get(download_url, stream=True) as r:
        if r.status_code != 200:
            print(f"[ERRO] Falha no download: {r.status_code}")
            sys.exit(1)

        baixado = 0
        with open(caminho_local, "wb") as f:
            for chunk in r.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    baixado += len(chunk)
                    if tamanho_total:
                        progresso = (baixado / tamanho_total) * 100
                        print(f"  -> {formatar_tamanho(baixado)} / {formatar_tamanho(tamanho_total)} ({progresso:.1f}%)")
                    else:
                        print(f"  -> {formatar_tamanho(baixado)}")

    print(f"[OK] Download concluido: {caminho_local}")


def download_pasta_recursivo(caminho_remoto: str, destino_local: str, nivel: int = 0):
    """Baixa recursivamente todos os arquivos de uma pasta, recriando a estrutura local."""
    itens    = listar_itens(caminho_remoto)
    arquivos = [i for i in itens if "folder" not in i]
    pastas   = [i for i in itens if "folder" in i]
    indent   = "  " * nivel

    os.makedirs(destino_local, exist_ok=True)

    if not itens:
        print(f"{indent}[INFO] Pasta vazia: {caminho_remoto}")
        return

    if arquivos:
        print(f"{indent}[INFO] {len(arquivos)} arquivo(s) em: {caminho_remoto}")
        for item in arquivos:
            nome        = item["name"]
            remoto_item = f"{caminho_remoto.rstrip('/')}/{nome}"
            print(f"\n{indent}v {nome}")
            download_arquivo(remoto_item, destino_local)

    if pastas:
        print(f"\n{indent}[INFO] {len(pastas)} subpasta(s) em: {caminho_remoto}")
        for pasta in pastas:
            nome_pasta      = pasta["name"]
            remoto_subpasta = f"{caminho_remoto.rstrip('/')}/{nome_pasta}"
            local_subpasta  = os.path.join(destino_local, nome_pasta)
            print(f"\n{indent}+-- Entrando em: {nome_pasta}/")
            download_pasta_recursivo(remoto_subpasta, local_subpasta, nivel + 1)


def download(caminho_remoto: str, destino_local: str):
    url_meta  = f"{drive_url(caminho_remoto)}"
    resp_meta = requests.get(url_meta, headers=headers())
    if resp_meta.status_code != 200:
        handle_error(resp_meta, "Download")

    meta = resp_meta.json()

    if "folder" in meta:
        print(f"\n[INFO] Iniciando download recursivo de: {caminho_remoto}")
        print(f"[INFO] Destino local: {destino_local}\n")
        download_pasta_recursivo(caminho_remoto, destino_local)
        print(f"\n[OK] Download recursivo concluido: {destino_local}")
    else:
        download_arquivo(caminho_remoto, destino_local)


# ---------------------------------------------
# Delete
# ---------------------------------------------

def deletar(caminho_remoto: str):
    """Deleta um arquivo ou pasta inteira (incluindo todo o conteudo)."""
    url  = f"{drive_url(caminho_remoto)}"
    resp = requests.delete(url, headers=headers())

    if resp.status_code == 204:
        print(f"[OK] Item deletado: {caminho_remoto}")
    else:
        handle_error(resp, "Deletar")


def deletar_conteudo(caminho_remoto: str):
    """Deleta apenas o conteudo de uma pasta, mantendo a pasta em si."""
    itens = listar_itens(caminho_remoto)

    if not itens:
        print(f"[INFO] Pasta ja esta vazia: {caminho_remoto}")
        return

    print(f"[INFO] {len(itens)} item(s) encontrado(s) em: {caminho_remoto}")
    erros = 0

    for idx, item in enumerate(itens, start=1):
        nome        = item["name"]
        remoto_item = f"{caminho_remoto.rstrip('/')}/{nome}"
        tipo        = "pasta" if "folder" in item else "arquivo"

        resp = requests.delete(f"{drive_url(remoto_item)}", headers=headers())

        if resp.status_code == 204:
            print(f"  [{idx}/{len(itens)}] [OK] {tipo}: {nome}")
        else:
            print(f"  [{idx}/{len(itens)}] [ERRO] {tipo}: {nome} - HTTP {resp.status_code}")
            erros += 1

    if erros == 0:
        print(f"\n[OK] Todo o conteudo de '{caminho_remoto}' foi removido. A pasta foi mantida.")
    else:
        print(f"\n[AVISO] Concluido com {erros} erro(s). A pasta '{caminho_remoto}' foi mantida.")


# ---------------------------------------------
# CLI
# ---------------------------------------------

def validar_env():
    faltando = [v for v in ("CLIENT_ID", "CLIENT_SECRET", "TENANT_ID", "USER_ID") if not os.getenv(v)]
    if faltando:
        print(f"[ERRO] Variaveis ausentes no .env: {', '.join(faltando)}")
        sys.exit(1)


def main():
    validar_env()

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

    args = parser.parse_args()

    if args.list is not None:
        listar(args.list or None)
    elif args.upload:
        upload(args.upload[0], args.upload[1])
    elif args.download:
        download(args.download[0], args.download[1])
    elif args.delete:
        deletar(args.delete)
    elif args.delete_contents:
        deletar_conteudo(args.delete_contents)


if __name__ == "__main__":
    main()