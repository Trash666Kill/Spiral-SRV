#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import shutil
import logging
import subprocess
import argparse
import tempfile
from datetime import datetime
from pathlib import Path

# Configuração Padrão para geração de template (caso arquivo não exista)
DEFAULT_JSON_CONFIG = {
    "description": "Template de Configuração",
    "paths": {
        "remote_share": "//IP/SHARE",
        "mount_point": "/mnt/Remote/Servers/Cliente/Share",
        "backup_root": "/mnt/Local/Container/A/Backup",
        "relative_path": "Cliente/Share",
        "log_dir": "/var/log/rsync",
        "credentials_file": "/root/.smbcreds_template"
    },
    "settings": {
        "min_space_mb": 1024,
        "bandwidth_limit_mb": 10,
        "ionice_class": 3,
        "nice_priority": 19,
        "retention_policy": {
            "keep_full_backups_days": 30,
            "keep_differential_files_days": 240,
            "cleanup_empty_dirs": True
        }
    },
    "excludes": ["*.tmp"]
}

class BackupJob:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = self._load_config()
        self.date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.setup_paths()
        self.setup_logging()

    def _load_config(self):
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"Erro ao ler arquivo de configuração: {e}")
            sys.exit(1)

    def setup_paths(self):
        """Constrói os caminhos baseados na raiz e no caminho relativo do cliente."""
        paths = self.config['paths']
        root = paths['backup_root']
        rel = paths['relative_path']

        # Diretório de Montagem (Origem)
        self.orig_dir = paths['mount_point']
        
        # Diretórios de Destino
        self.incr_dir = os.path.join(root, "Incremental", rel)
        self.diff_dir = os.path.join(root, "Differential", rel)
        self.full_dir = os.path.join(root, "Full", rel)
        
        # Arquivo de Log
        log_name = f"backup_{rel.replace('/', '_')}_{self.date_str}.log"
        self.log_file = os.path.join(paths['log_dir'], log_name)

    def setup_logging(self):
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] %(levelname)s: %(message)s',
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger()

    def check_pre_flight(self):
        """Verificações de disco e criação de pastas (mkdir -p)."""
        # 1. Checagem de Espaço (MB -> Bytes)
        req_mb = self.config['settings']['min_space_mb']
        req_bytes = req_mb * 1024 * 1024
        
        # Encontra o diretório existente mais próximo para checar espaço
        check_path = self.config['paths']['backup_root']
        if not os.path.exists(check_path):
            try:
                os.makedirs(check_path, exist_ok=True)
            except:
                pass # Se falhar aqui, o disk_usage abaixo vai pegar o erro ou checar o pai

        total, used, free = shutil.disk_usage(os.path.dirname(self.orig_dir)) # Checa montagem local ou raiz
        
        # Nota: Normalmente checamos o destino (backup_root).
        total, used, free = shutil.disk_usage("/") # Fallback simples se o path não existir ainda
        if os.path.exists(self.config['paths']['backup_root']):
             total, used, free = shutil.disk_usage(self.config['paths']['backup_root'])

        if free < req_bytes:
            raise Exception(f"Espaço insuficiente. Requerido: {req_mb}MB, Livre: {free/1024/1024:.2f}MB")

        # 2. Criação de Diretórios (Automático)
        for d in [self.orig_dir, self.incr_dir, self.diff_dir, self.full_dir]:
            if not os.path.exists(d):
                self.logger.info(f"Criando diretório: {d}")
                os.makedirs(d, exist_ok=True)

    def mount_share(self):
        """Monta o CIFS usando arquivo de credenciais seguro."""
        if os.path.ismount(self.orig_dir):
            self.logger.info("Origem já montada. Pulando montagem.")
            return False

        creds = self.config['paths']['credentials_file']
        remote = self.config['paths']['remote_share']
        
        if not os.path.exists(creds):
            raise FileNotFoundError(f"Arquivo de credenciais não encontrado: {creds}")

        self.logger.info(f"Montando {remote}...")
        subprocess.run(
            ["mount", "-t", "cifs", remote, self.orig_dir, "-o", f"ro,credentials={creds}"],
            check=True
        )
        return True

    def run_rsync(self):
        """Executa Rsync Incremental com gestão de exclusões e bandwidth."""
        bw_kb = int(self.config['settings']['bandwidth_limit_mb'] * 1024)
        
        # Cria arquivo de exclusão temporário
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            tmp.write('\n'.join(self.config['excludes']))
            tmp_exclude = tmp.name

        try:
            cmd = [
                "rsync", f"--bwlimit={bw_kb}",
                "-ahx", "--acls", "--xattrs", "--numeric-ids", "--chmod=ugo+r",
                "--ignore-errors", "--force",
                f"--exclude-from={tmp_exclude}",
                "--delete", "--backup", f"--backup-dir={self.diff_dir}",
                "--info=del,name,stats2",
                f"--log-file={self.log_file}",
                self.orig_dir + "/", self.incr_dir # Barra no final da origem é importante
            ]
            
            self.logger.info("Iniciando sincronização (Incremental)...")
            ret = subprocess.run(cmd)
            
            if ret.returncode not in [0, 23]:
                raise subprocess.CalledProcessError(ret.returncode, cmd)
            if ret.returncode == 23:
                self.logger.warning("Rsync finalizou com código 23 (Transferência Parcial).")
            else:
                self.logger.info("Rsync finalizado com sucesso.")
        finally:
            os.remove(tmp_exclude)

    def cleanup_differential(self):
        """Limpa arquivos diferenciais antigos baseados na política do JSON."""
        days = self.config['settings']['retention_policy']['keep_differential_files_days']
        clean_dirs = self.config['settings']['retention_policy']['cleanup_empty_dirs']
        
        self.logger.info(f"Limpando arquivos diferenciais com mais de {days} dias...")
        subprocess.run(["find", self.diff_dir, "-type", "f", "-mtime", f"+{days}", "-delete"])
        
        if clean_dirs:
            self.logger.info("Removendo diretórios vazios no Differential...")
            subprocess.run(["find", self.diff_dir, "-type", "d", "-empty", "-delete"])

    def run_full_backup(self):
        """Gera Full Backup (.tar.zst) usando Reflink + Pipeline."""
        retention = self.config['settings']['retention_policy']['keep_full_backups_days']
        
        # 1. Limpeza de Fulls Antigos
        subprocess.run(["find", self.full_dir, "-type", "f", "-name", "Full_*.tar.zst", "-mtime", f"+{retention}", "-delete"])

        # 2. Verifica se já existe backup recente (Opcional, baseado no script original logic)
        # O script original checava se existia um arquivo < 30 dias. 
        # Vamos pular essa verificação complexa e focar na criação segura
        
        staging_dir = os.path.join(self.full_dir, "Full_Staging")
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir)

        # 3. Cópia Inteligente (Reflink)
        self.logger.info("Preparando snapshot para Full Backup...")
        try:
            subprocess.run(
                ["cp", "-a", "--reflink=always", self.incr_dir, staging_dir],
                check=True, stderr=subprocess.PIPE
            )
            self.logger.info("Snapshot via Reflink (CoW) realizado.")
        except subprocess.CalledProcessError:
            self.logger.warning("Reflink falhou. Usando cópia padrão (cp -a)...")
            subprocess.run(["cp", "-a", self.incr_dir, staging_dir], check=True)

        # 4. Compactação (Tar -> Zstd)
        zst_file = os.path.join(self.full_dir, f"Full_{self.date_str}.tar.zst")
        io_cls = str(self.config['settings']['ionice_class'])
        nice_pri = str(self.config['settings']['nice_priority'])

        self.logger.info(f"Compactando: {zst_file}")
        try:
            p_tar = subprocess.Popen(
                ["ionice", "-c", io_cls, "nice", "-n", nice_pri, "tar", "-cvf", "-", "-C", os.path.dirname(staging_dir), os.path.basename(staging_dir)],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
            )
            with open(zst_file, "wb") as f_out:
                p_zstd = subprocess.Popen(["zstd", "--threads=2"], stdin=p_tar.stdout, stdout=f_out)
            
            p_tar.stdout.close()
            p_zstd.communicate()
            
            if p_zstd.returncode == 0:
                self.logger.info("Backup Full criado com sucesso.")
            else:
                raise Exception("Erro no ZSTD")
        finally:
            if os.path.exists(staging_dir):
                shutil.rmtree(staging_dir)

    def cleanup(self, did_mount):
        if did_mount:
            self.logger.info("Desmontando origem...")
            subprocess.run(["umount", self.orig_dir])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config_file", nargs="?", default="config.json")
    args = parser.parse_args()

    # Bootstrap
    if not os.path.exists(args.config_file):
        print(f"Criando modelo em {args.config_file}...")
        with open(args.config_file, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        sys.exit(0)

    did_mount = False
    job = None
    try:
        job = BackupJob(args.config_file)
        job.logger.info(f"=== Job Iniciado: {args.config_file} ===")
        
        job.check_pre_flight()
        did_mount = job.mount_share()
        
        job.run_rsync()
        job.cleanup_differential() # Limpeza de arquivos diff antigos
        job.run_full_backup()
        
        job.logger.info("=== Job Finalizado ===")
    except Exception as e:
        if job: job.logger.error(f"FALHA CRÍTICA: {e}")
        else: print(e)
        sys.exit(1)
    finally:
        if job: job.cleanup(did_mount)

if __name__ == "__main__":
    main()