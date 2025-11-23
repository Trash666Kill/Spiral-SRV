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
import textwrap
from datetime import datetime

# --- Texto de Ajuda Detalhado ---
HELP_TEXT = textwrap.dedent("""
    \033[1mLÓGICA DE FUNCIONAMENTO:\033[0m
    Este script utiliza uma estrutura de diretórios centrada no cliente (Client-Centric).
    A estrutura de pastas local é automaticamente derivada do campo 'remote_share' 
    (Ex: //192.168.0.100/Share -> 192_168_0_100/Share).

    \033[1m1. Estrutura de Diretórios:\033[0m
       - O nome da pasta local é definido pelo endereço IP do servidor (sanitizado).
       - Estrutura: [Backup Root] / [IP_sanitizado/Share] / {Incremental, Differential, Full}

    \033[1m2. Backup Full (Snapshot + Compressão):\033[0m
       - Utiliza 'Reflink' (Copy-on-Write) para cópia instantânea.
       - Se já existir um Full válido (conforme retenção), a criação é PULADA.

    \033[1m3. Limpeza Automática:\033[0m
       - Logs antigos, arquivos diferenciais e arquivos Full expirados são removidos com base na política.

    \033[1mEXEMPLOS:\033[0m
       $ python3 filesbkp.py --init
       $ python3 filesbkp.py clientes/sugisawa.json --debug
""")

# --- Configuração Padrão ---
DEFAULT_JSON_CONFIG = {
    "description": "Template de Configuração",
    "credentials": {
        "username": "usuario",
        "password": "senha",
        "domain": "dominio.local"
    },
    "paths": {
        "remote_share": "//192.168.0.100/Dados/Share",
        "mount_point": "/mnt/Remote/MountPoint",
        "backup_root": "/mnt/Backup",
        "log_dir": "/var/log/rsync"
    },
    "settings": {
        "mount_options": "ro",
        "min_space_mb": 1024,
        "bandwidth_limit_mb": 10,
        "ionice_class": 3,
        "nice_priority": 19,
        "rsync_flags": [
            "-ahx", "--acls", "--xattrs", "--numeric-ids", 
            "--chmod=ugo+r", "--ignore-errors", "--force", "--delete"
        ],
        "retention_policy": {
            "keep_logs_days": 30,
            "keep_full_backups_days": 30,
            "keep_differential_files_days": 240,
            "cleanup_empty_dirs": True
        }
    },
    "excludes": ["*.tmp", "Thumbs.db"]
}

class BackupJob:
    def __init__(self, config_path, debug=False):
        self.config_path = config_path
        self.debug = debug
        self.config = self._load_config()
        self.date_str = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
        self.setup_paths()
        self.setup_logging()

    def _load_config(self):
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[\033[91mERRO\033[0m] Erro no JSON: {e}")
            sys.exit(1)

    def setup_paths(self):
        paths = self.config['paths']
        root = paths['backup_root']
        
        # O ponto de montagem (source) não é afetado
        self.orig_dir = paths['mount_point']

        # --- LÓGICA DE DERIVAÇÃO DE CAMINHO (Máxima Automação) ---
        
        # 1. Obtém o nome base do diretório removendo o prefixo de rede (//)
        remote_path = paths['remote_share']
        rel = remote_path[2:] # Remove o prefixo "//"

        # 2. Sanitiza o nome (troca IPs por underscores: 192.168.0.100 -> 192_168_0_100)
        rel_sanitized = rel.replace('.', '_')
        
        # 3. Define a raiz deste cliente: [backup_root] / [remote_share_sanitized]
        self.client_root = os.path.join(root, rel_sanitized)

        # 4. Define as pastas de TIPO (Incremental/Differential/Full) dentro da raiz do cliente
        self.incr_dir = os.path.join(self.client_root, "Incremental")
        self.diff_dir = os.path.join(self.client_root, "Differential")
        self.full_dir = os.path.join(self.client_root, "Full")
        
        # 5. Nome seguro para logs
        self.safe_name = rel_sanitized.replace('/', '_').replace('\\', '_')
        
        log_name = f"backup_{self.safe_name}_{self.date_str}.log"
        self.log_file = os.path.join(paths['log_dir'], log_name)

    def setup_logging(self):
        try:
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
        except Exception as e:
            print(f"[\033[91mERRO\033[0m] Erro no Log: {e}")
            sys.exit(1)

    def _run_cmd(self, cmd, check=True, **kwargs):
        if self.debug:
            cmd_str = ' '.join([str(x) for x in cmd])
            self.logger.info(f"[\033[36mDEBUG\033[0m] Executando: {cmd_str}")
        return subprocess.run(cmd, check=check, **kwargs)

    def check_pre_flight(self):
        req_mb = self.config['settings']['min_space_mb']
        req_bytes = req_mb * 1024 * 1024
        
        check_path = self.config['paths']['backup_root']
        while not os.path.exists(check_path):
            check_path = os.path.dirname(check_path)
            if not check_path or check_path == "/": break
        if not os.path.exists(check_path): check_path = "/"

        total, used, free = shutil.disk_usage(check_path)
        
        if free < req_bytes:
            raise Exception(f"Espaço insuficiente. Livre: {free/1024/1024:.2f} MB")
        
        self.logger.info(f"Disco OK. Livre: {free/1024/1024:.2f} MB")

        dirs_to_create = [self.orig_dir, self.incr_dir, self.diff_dir, self.full_dir]
        for d in dirs_to_create:
            if not os.path.exists(d):
                self.logger.info(f"Criando: {d}")
                os.makedirs(d, exist_ok=True)

    def mount_share(self):
        if os.path.ismount(self.orig_dir):
            self.logger.info("Já montado. Pulando.")
            return False 

        creds = self.config.get('credentials', {})
        user = creds.get('username')
        password = creds.get('password')
        domain = creds.get('domain')
        
        remote = self.config['paths']['remote_share']
        opts = self.config['settings'].get('mount_options', 'ro')

        if not user or not password:
            raise ValueError("Credenciais incompletas no JSON.")

        self.logger.info(f"Montando {remote} (Opções: {opts})...")
        
        auth_opts = f"username={user},password={password}"
        if domain: auth_opts += f",domain={domain}"
        final_opts = f"{opts},{auth_opts}"
        
        cmd = ["mount", "-t", "cifs", remote, self.orig_dir, "-o", final_opts]
        
        try:
            self._run_cmd(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            raise Exception(f"Erro no mount (Exit Code {e.returncode})")

    def run_rsync(self):
        bw_kb = int(self.config['settings']['bandwidth_limit_mb'] * 1024)
        
        default_flags = ["-ahx", "--acls", "--xattrs", "--numeric-ids", "--chmod=ugo+r", "--ignore-errors", "--force", "--delete"]
        rsync_flags = self.config['settings'].get('rsync_flags', default_flags)
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as tmp:
            if 'excludes' in self.config:
                tmp.write('\n'.join(self.config['excludes']))
            tmp_exclude = tmp.name

        try:
            src = self.orig_dir if self.orig_dir.endswith('/') else self.orig_dir + '/'
            
            cmd = ["rsync", f"--bwlimit={bw_kb}"]
            cmd.extend(rsync_flags)
            
            cmd.extend([
                f"--exclude-from={tmp_exclude}",
                "--backup", 
                f"--backup-dir={self.diff_dir}",
                "--info=del,name,stats2", 
                f"--log-file={self.log_file}",
                src, 
                self.incr_dir
            ])
            
            self.logger.info("Executando Rsync...")
            res = self._run_cmd(cmd, check=False)
            
            if res.returncode == 0:
                self.logger.info("Rsync: Sucesso.")
            elif res.returncode == 23:
                self.logger.warning("Rsync: Aviso (Code 23).")
            else:
                raise subprocess.CalledProcessError(res.returncode, cmd)
        finally:
            if os.path.exists(tmp_exclude): os.remove(tmp_exclude)

    def cleanup_differential(self):
        policy = self.config['settings'].get('retention_policy', {})
        days = policy.get('keep_differential_files_days', 240)
        
        self.logger.info(f"Limpando Diff > {days} dias...")
        self._run_cmd(["find", self.diff_dir, "-type", "f", "-mtime", f"+{days}", "-delete"], check=False)
        
        if policy.get('cleanup_empty_dirs', True):
            self._run_cmd(["find", self.diff_dir, "-type", "d", "-empty", "-delete"], check=False)

    def run_full_backup(self):
        policy = self.config['settings'].get('retention_policy', {})
        retention = policy.get('keep_full_backups_days', 30)

        self.logger.info(f"Verificando Fulls antigos (> {retention} dias)...")
        self._run_cmd(["find", self.full_dir, "-type", "f", "-name", "Full_*.tar.zst", "-mtime", f"+{retention}", "-delete"], check=False)

        self.logger.info(f"Verificando validade do Full atual (< {retention} dias)...")
        cmd_check = [
            "find", self.full_dir, 
            "-type", "f", 
            "-name", "Full_*.tar.zst", 
            "-mtime", f"-{retention}", 
            "-print", "-quit"
        ]
        
        res = self._run_cmd(cmd_check, check=False, capture_output=True, text=True)
        if res.stdout and res.stdout.strip():
            recent_file = res.stdout.strip()
            self.logger.info(f"Backup Full válido encontrado ({recent_file}). Mantendo estrutura atual.")
            return

        persistent_full_dir = os.path.join(self.full_dir, "Full")

        if os.path.exists(persistent_full_dir):
            self.logger.info(f"Removendo diretório '{persistent_full_dir}' antigo para atualização...")
            shutil.rmtree(persistent_full_dir)

        self.logger.info("Criando novo Snapshot 'Full' (Reflink)...")
        try:
            cmd_reflink = ["cp", "-a", "--reflink=always", self.incr_dir, persistent_full_dir]
            self._run_cmd(cmd_reflink, check=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError:
            self.logger.warning("Reflink falhou. Usando cp -a.")
            self._run_cmd(["cp", "-a", self.incr_dir, persistent_full_dir], check=True)

        filename = f"Full_{self.date_str}.tar.zst"
        zst_path = os.path.join(self.full_dir, filename)
        
        ionice = str(self.config['settings']['ionice_class'])
        nice = str(self.config['settings']['nice_priority'])

        self.logger.info(f"Compactando: {filename}")
        
        cmd_tar = [
            "ionice", "-c", ionice, "nice", "-n", nice, 
            "tar", "-cvf", "-", 
            "-C", self.full_dir, 
            "Full"
        ]
        cmd_zstd = ["zstd", "--threads=2"]

        if self.debug:
            self.logger.info(f"[\033[36mDEBUG\033[0m] Pipeline: {' '.join(cmd_tar)} | {' '.join(cmd_zstd)} > {zst_path}")

        try:
            p_tar = subprocess.Popen(cmd_tar, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            with open(zst_path, "wb") as f_out:
                p_zstd = subprocess.Popen(cmd_zstd, stdin=p_tar.stdout, stdout=f_out)

            p_tar.stdout.close()
            p_zstd.communicate()
            
            if p_zstd.returncode == 0:
                self.logger.info("Full Backup concluído. Diretório 'Full' mantido no disco.")
            else:
                raise Exception(f"Zstd falhou: {p_zstd.returncode}")
        
        except Exception as e:
            self.logger.error(f"Erro na compactação: {e}")
            raise

    def cleanup_logs(self):
        policy = self.config['settings'].get('retention_policy', {})
        days = policy.get('keep_logs_days', 30)
        log_dir = self.config['paths']['log_dir']
        
        log_pattern = f"backup_{self.safe_name}_*.log"
        
        self.logger.info(f"Limpando logs antigos de '{self.safe_name}' (> {days} dias)...")
        
        cmd = [
            "find", log_dir,
            "-name", log_pattern,
            "-type", "f",
            "-mtime", f"+{days}",
            "-delete"
        ]
        
        self._run_cmd(cmd, check=False)

    def cleanup(self, did_mount):
        if did_mount:
            self.logger.info("Desmontando...")
            self._run_cmd(["umount", self.orig_dir], check=False)

def main():
    parser = argparse.ArgumentParser(
        description="Gerenciador de Backup Corporativo",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    parser.add_argument("config_file", nargs="?", help="Caminho do arquivo JSON de configuração")
    parser.add_argument("--init", action="store_true", help="Cria um modelo de configuração padrão no local especificado")
    parser.add_argument("--debug", action="store_true", help="Exibe os comandos executados no shell para depuração")
    
    args = parser.parse_args()

    if args.init:
        target = args.config_file if args.config_file else "config_modelo.json"
        if os.path.exists(target): sys.exit(1)
        with open(target, 'w') as f: json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        print(f"Modelo criado: {target}"); sys.exit(0)

    if not args.config_file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.config_file):
        print(f"[\033[93mAVISO\033[0m] '{args.config_file}' não encontrado.")
        print(f"Criando modelo padrão...")
        with open(args.config_file, 'w') as f: json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        print("Arquivo criado. Edite-o e tente novamente.")
        sys.exit(0)

    job = None; did_mount = False
    try:
        job = BackupJob(args.config_file, debug=args.debug)
        job.logger.info(f"=== Job Iniciado: {args.config_file} ===")
        if args.debug: job.logger.info("MODO DEBUG ATIVADO")

        job.check_pre_flight()
        did_mount = job.mount_share()
        
        job.run_rsync()
        job.cleanup_differential()
        job.run_full_backup()
        job.cleanup_logs()
        
        job.logger.info("=== Sucesso ===")
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        if job: job.logger.error(f"FALHA: {e}")
        else: print(f"ERRO: {e}")
        sys.exit(1)
    finally:
        if job: job.cleanup(did_mount)

if __name__ == "__main__":
    main()