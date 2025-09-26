import os
import glob
import shutil
import logging
from datetime import datetime
import sys
import argparse
import mimetypes
import re
import time

# Configuração básica do logging (apenas console por padrão)
logging.basicConfig(
    format='%(asctime)s - %(levelname)s - %(message)s',
    level=logging.INFO,
    handlers=[logging.StreamHandler()]
)

# Inicializa o módulo mimetypes
mimetypes.init()

# Dicionário para converter número do mês para nome em português
MONTHS_PT = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro"
}

# Tipos de arquivos padrão
DEFAULT_FILE_TYPES = ["*.pdf", "*.txt", "*.docx", "*.jpg", "*.jpeg", "*.png", "*.gif", "*.bmp"]

def sanitize_filename(filename):
    """Remove caracteres inválidos para nomes de arquivos no Windows."""
    invalid_chars = r'[<>:"/\\|?*]'
    return re.sub(invalid_chars, '_', filename)

def get_unique_path(parent_dir, base_name):
    """Retorna um caminho único no diretório pai, adicionando dup1, dup2, etc., se necessário."""
    base_name = sanitize_filename(base_name)
    new_path = os.path.join(parent_dir, base_name)
    if not os.path.exists(new_path):
        return new_path
    
    counter = 1
    while True:
        new_name = f"{os.path.splitext(base_name)[0]}_dup{counter}{os.path.splitext(base_name)[1] if '.' in base_name else ''}"
        new_path = os.path.join(parent_dir, new_name)
        if not os.path.exists(new_path):
            return new_path
        counter += 1

def print_summary(source_dir):
    """Exibe um resumo da quantidade de arquivos e tipos MIME em cada diretório."""
    logging.info(f"Resumo da organização em: {source_dir}")
    
    for root, _, files in os.walk(source_dir):
        if files:  # Apenas diretórios com arquivos
            rel_path = os.path.relpath(root, source_dir)
            mime_counts = {}
            
            for file in files:
                file_path = os.path.join(root, file)
                mime_type, _ = mimetypes.guess_type(file_path)
                mime_type = mime_type if mime_type else "unknown"
                mime_counts[mime_type] = mime_counts.get(mime_type, 0) + 1
            
            logging.info(f"Diretório: {rel_path} - Total: {len(files)} arquivos")
            for mime_type, count in mime_counts.items():
                logging.info(f"  {mime_type}: {count}")

def organize_files(source_dir, file_types, debug=False, log_file=None, move=False, watch=False):
    # Ajusta o nível de log baseado no parâmetro debug
    if debug:
        logging.getLogger().setLevel(logging.DEBUG)
    else:
        logging.getLogger().setLevel(logging.INFO)

    # Adiciona FileHandler se --log for especificado
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logging.getLogger().addHandler(file_handler)

    # Valida o diretório fonte
    if not os.path.isdir(source_dir):
        logging.error(f"O diretório {source_dir} não existe ou não é acessível.")
        sys.exit(1)

    logging.info(f"Início da execução do script para o diretório: {source_dir}")

    # Muda para o diretório fonte
    try:
        os.chdir(source_dir)
        logging.debug(f"Diretório de trabalho alterado para: {os.getcwd()}")
    except Exception as e:
        logging.error(f"Erro ao mudar para o diretório {source_dir}: {e}")
        sys.exit(1)

    def process_files():
        """Processa arquivos na raiz ou em subdiretórios, dependendo do modo."""
        if watch:
            # No modo --watch, processa apenas a raiz
            files = [f for f in os.listdir(".") if os.path.isfile(f) and any(f.lower().endswith(ext[1:].lower()) for ext in file_types)]
            for file in files:
                process_single_file(file, source_dir, True)
        else:
            # No modo normal, processa raiz e subdiretórios
            for root, dirs, files in os.walk(".", topdown=True):
                relevant_files = [f for f in files if any(f.lower().endswith(ext[1:].lower()) for ext in file_types)]
                if relevant_files:
                    subdir_path = os.path.join(source_dir, root)
                    dest_subdir = get_unique_path(source_dir, os.path.basename(root) if root != "." else "")
                    for file in relevant_files:
                        file_path = os.path.join(root, file)
                        process_single_file(file_path, dest_subdir, move)

    def process_single_file(file_path, dest_subdir, move_file):
        """Processa um único arquivo, movendo ou copiando para o destino."""
        logging.debug(f"Analisando arquivo: {file_path}")
        
        # Obtém a data de modificação do arquivo
        try:
            mod_time = os.path.getmtime(file_path)
            file_date = datetime.fromtimestamp(mod_time)
            file_year = file_date.year
            file_month = MONTHS_PT[file_date.month]
        except Exception as e:
            logging.error(f"Erro ao obter data de modificação de {file_path}: {e}")
            return

        # Cria o diretório de destino
        dest_dir = os.path.join(dest_subdir, str(file_year), file_month)
        try:
            os.makedirs(dest_dir, exist_ok=True)
            logging.debug(f"Diretório criado ou já existente: {dest_dir}")
        except Exception as e:
            logging.error(f"Erro ao criar diretório {dest_dir}: {e}")
            return

        # Garante um nome único para o arquivo no diretório destino
        file_name = os.path.basename(file_path)
        dest_path = get_unique_path(dest_dir, file_name)
        try:
            rel_source = os.path.relpath(file_path, source_dir)
            rel_dest = os.path.relpath(dest_path, source_dir)
            if move_file:
                shutil.move(file_path, dest_path)
                logging.info(f"Movido: {rel_source} -> {rel_dest}")
            else:
                shutil.copy2(file_path, dest_path)
                logging.info(f"Copiado: {rel_source} -> {rel_dest}")
        except Exception as e:
            logging.error(f"Erro ao {'mover' if move_file else 'copiar'} {rel_source} para {rel_dest}: {e}")

    if watch:
        # Modo --watch: loop infinito, verifica raiz a cada 10 segundos
        logging.info("Modo watch ativado: verificando novos arquivos na raiz a cada 10 segundos...")
        try:
            while True:
                process_files()
                time.sleep(10)
        except KeyboardInterrupt:
            logging.info("Execução interrompida pelo usuário (Ctrl+C)")
            print_summary(source_dir)
            sys.exit(0)
    else:
        # Modo normal: processa uma vez e exibe resumo
        process_files()
        print_summary(source_dir)

    logging.info("Fim da execução do script")

if __name__ == "__main__":
    # Configura o parser de argumentos
    parser = argparse.ArgumentParser(description="Organiza arquivos por ano e mês, preservando subdiretórios na raiz.")
    parser.add_argument("directory", help="Diretório fonte dos arquivos")
    parser.add_argument("--debug", action="store_true", help="Ativa logs detalhados")
    parser.add_argument("--log", metavar="FILE", help="Gera arquivo de log com o nome especificado")
    parser.add_argument("--move", action="store_true", help="Move arquivos em vez de copiá-los (modo normal)")
    parser.add_argument("--watch", action="store_true", help="Monitora a raiz do diretório a cada 10 segundos, movendo arquivos")
    parser.add_argument("--file-types", metavar="TYPES", help="Tipos de arquivos a processar (ex.: '*.pdf,*.txt,*.jpg')")
    
    args = parser.parse_args()
    
    # Processa file-types
    file_types = DEFAULT_FILE_TYPES
    if args.file_types:
        file_types = [t.strip() for t in args.file_types.split(",") if t.strip()]
        if not file_types:
            logging.error("Nenhum tipo de arquivo válido fornecido em --file-types. Usando padrão.")
            file_types = DEFAULT_FILE_TYPES
    
    # Executa a função com os argumentos
    organize_files(args.directory, file_types, args.debug, args.log, args.move, args.watch)