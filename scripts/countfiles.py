import os
import sys
import argparse
from collections import defaultdict
import mimetypes
import logging

def setup_logging():
    """Configura o logging para exibir mensagens no console."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.StreamHandler(sys.stdout)]
    )

def count_files_by_type(directory):
    """Conta arquivos por tipo MIME e calcula o tamanho total por tipo."""
    file_types = defaultdict(lambda: {'count': 0, 'size': 0})
    mimetypes.init()

    try:
        for root, _, files in os.walk(directory):
            for filename in files:
                file_path = os.path.join(root, filename)
                mime_type, _ = mimetypes.guess_type(file_path)
                if mime_type:
                    main_type = mime_type.split('/')[0]
                else:
                    main_type = 'unknown'
                file_types[main_type]['count'] += 1
                file_types[main_type]['size'] += os.path.getsize(file_path)
        return file_types
    except Exception as e:
        logging.error(f"Erro ao processar o diretório: {e}")
        return None

def count_files_by_extension(directory, output_file=None):
    """Conta arquivos por extensão, calcula tamanho total por extensão e, se especificado, salva detalhes no arquivo de saída."""
    file_extensions = defaultdict(lambda: {'count': 0, 'size': 0})
    file_list = []
    mimetypes.init()

    try:
        logging.info(f"Iniciando contagem de arquivos no diretório: {directory}")
        for root, _, files in os.walk(directory):
            for filename in files:
                file_path = os.path.join(root, filename)
                _, ext = os.path.splitext(filename)
                ext = ext.lower() if ext else "sem_extensao"
                file_extensions[ext]['count'] += 1
                file_extensions[ext]['size'] += os.path.getsize(file_path)
                file_list.append(file_path)
                logging.debug(f"Arquivo encontrado: {filename} (extensão: {ext})")
        logging.info("Contagem de arquivos concluída.")

        # Exibir resultados no console
        print("\nQuantidade e tamanho de arquivos por extensão:")
        print("-" * 45)
        for ext, data in sorted(file_extensions.items()):
            size_mb = data['size'] / (1024 * 1024)  # Converter bytes para MB
            print(f"{ext if ext != 'sem_extensao' else 'Sem extensão'}: {data['count']} arquivos, {size_mb:.2f} MB")
        print("-" * 45)
        total_files = sum(data['count'] for data in file_extensions.values())
        total_size = sum(data['size'] for data in file_extensions.values()) / (1024 * 1024)  # Converter bytes para MB
        print(f"Total de arquivos: {total_files}, {total_size:.2f} MB")
        logging.info(f"Total de arquivos processados: {total_files}")

        # Salvar no arquivo de saída, se especificado
        if output_file:
            try:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write("Quantidade e tamanho de arquivos por extensão:\n")
                    f.write("-" * 45 + "\n")
                    for ext, data in sorted(file_extensions.items()):
                        size_mb = data['size'] / (1024 * 1024)  # Converter bytes para MB
                        f.write(f"{ext if ext != 'sem_extensao' else 'Sem extensão'}: {data['count']} arquivos, {size_mb:.2f} MB\n")
                    f.write("-" * 45 + "\n")
                    f.write(f"Total de arquivos: {total_files}, {total_size:.2f} MB\n")
                    f.write("\nLista de arquivos:\n")
                    f.write("-" * 45 + "\n")
                    for file_path in file_list:
                        f.write(f"{file_path}\n")
                logging.info(f"Resultados salvos em: {output_file}")
            except Exception as e:
                logging.error(f"Erro ao salvar no arquivo {output_file}: {e}")

        return file_extensions
    except Exception as e:
        logging.error(f"Erro ao processar o diretório: {e}")
        return None

def main():
    # Configura o parser de argumentos
    parser = argparse.ArgumentParser(description="Conta arquivos por tipo ou extensão.")
    parser.add_argument("directory", nargs='?', default=os.getcwd(), help="Diretório para analisar (padrão: diretório atual)")
    parser.add_argument("--detailed", action="store_true", help="Exibe contagem por extensão em vez de tipo MIME")
    parser.add_argument("--out", type=str, help="Arquivo para salvar a saída detalhada")
    args = parser.parse_args()

    # Configura o logging
    setup_logging()

    directory = args.directory
    if not os.path.exists(directory):
        logging.error(f"O diretório {directory} não existe.")
        return

    if args.detailed:
        # Modo detalhado: contagem por extensão
        file_counts = count_files_by_extension(directory, args.out)
    else:
        # Modo padrão: contagem por tipo MIME
        file_counts = count_files_by_type(directory)
        if file_counts:
            logging.info("\nExibindo resultados:")
            print("\nQuantidade e tamanho de arquivos por tipo:")
            print("-" * 40)
            for file_type, data in sorted(file_counts.items()):
                size_mb = data['size'] / (1024 * 1024)  # Converter bytes para MB
                print(f"{file_type}: {data['count']} arquivos, {size_mb:.2f} MB")
            print("-" * 40)
            total_files = sum(data['count'] for data in file_counts.values())
            total_size = sum(data['size'] for data in file_counts.values()) / (1024 * 1024)  # Converter bytes para MB
            print(f"Total de arquivos: {total_files}, {total_size:.2f} MB")
            logging.info(f"Total de arquivos processados: {total_files}")

if __name__ == "__main__":
    main()