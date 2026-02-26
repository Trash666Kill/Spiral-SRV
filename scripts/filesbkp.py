#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import json
import shutil
import stat
import signal
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
        "transfer_rate_pv": "10m",
        "ionice_class": 3,
        "nice_priority": 19,
        "rsync_user": "root",
        "rsync_flags": [
            "-ahx", "--acls", "--xattrs", "--numeric-ids",
            "--chmod=ugo+r", "--ignore-errors", "--force", "--delete",
            "--info=del,name,stats2"
        ],
        "retention_policy": {
            "keep_logs_count": 31,
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

    # ------------------------------------------------------------------
    # Carregamento e validação do JSON
    # ------------------------------------------------------------------

    def _load_config(self):
        try:
            with open(self.config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            print(f"[\033[91mERRO\033[0m] Erro no JSON: {e}")
            sys.exit(1)

    # ------------------------------------------------------------------
    # Configuração de caminhos
    # ------------------------------------------------------------------

    def setup_paths(self):
        paths = self.config['paths']
        root = paths['backup_root']

        self.orig_dir = paths['mount_point']

        remote_path = paths['remote_share']
        rel = remote_path[2:]                          # Remove prefixo "//"
        rel_sanitized = rel.replace('.', '_')

        self.client_root = os.path.join(root, rel_sanitized)
        self.incr_dir    = os.path.join(self.client_root, "Incremental")
        self.diff_dir    = os.path.join(self.client_root, "Differential")
        self.full_dir    = os.path.join(self.client_root, "Full")

        self.safe_name = rel_sanitized.replace('/', '_').replace('\\', '_')

        log_name     = f"backup_{self.safe_name}_{self.date_str}.log"
        self.log_file = os.path.join(paths['log_dir'], log_name)

    # ------------------------------------------------------------------
    # Logging
    # ------------------------------------------------------------------

    def setup_logging(self):
        try:
            os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
            logging.basicConfig(
                level=logging.DEBUG if self.debug else logging.INFO,
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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    # Padrões de opções de mount que contêm credenciais e devem ser ocultados.
    _SENSITIVE_MOUNT_KEYS = {"username", "password", "domain"}

    @staticmethod
    def _redact_mount_opts(opts_str: str) -> str:
        """
        Recebe a string de opções do mount (ex: 'ro,username=foo,password=bar')
        e substitui o VALOR de cada chave sensível por '***'.
        """
        parts = opts_str.split(',')
        redacted = []
        for part in parts:
            if '=' in part:
                key, _ = part.split('=', 1)
                if key.strip() in BackupJob._SENSITIVE_MOUNT_KEYS:
                    redacted.append(f"{key}=***")
                    continue
            redacted.append(part)
        return ','.join(redacted)

    def _redact_cmd(self, cmd: list) -> str:
        """
        Converte a lista de argumentos do comando em string para log,
        ocultando o valor do argumento '-o' quando ele contiver credenciais
        de mount (username/password/domain).
        """
        parts = [str(x) for x in cmd]
        result = []
        skip_next = False
        for i, part in enumerate(parts):
            if skip_next:
                result.append(self._redact_mount_opts(part))
                skip_next = False
            elif part == '-o' and i + 1 < len(parts):
                result.append(part)
                skip_next = True   # próximo token é o valor de -o
            else:
                result.append(part)
        return ' '.join(result)

    def _run_cmd(self, cmd, check=True, **kwargs):
        if self.debug:
            self.logger.debug(
                f"[\033[36mDEBUG\033[0m] Executando: {self._redact_cmd(cmd)}"
            )
        return subprocess.run(cmd, check=check, **kwargs)

    def _check_config_file_permissions(self):
        """
        FIX 1 — Alerta se o arquivo de configuração (que contém credenciais)
        estiver com permissões abertas (leitura/escrita por grupo ou outros).
        """
        file_stat = os.stat(self.config_path)
        mode = file_stat.st_mode
        if mode & (stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH):
            self.logger.warning(
                f"AVISO DE SEGURANÇA: '{self.config_path}' contém credenciais e está "
                f"acessível por grupo/outros (modo {oct(mode & 0o777)}). "
                f"Execute: chmod 600 {self.config_path}"
            )

    # ------------------------------------------------------------------
    # Verificação de dependências
    # ------------------------------------------------------------------

    REQUIRED_TOOLS = [
        "rsync", "mount", "umount", "find", "du", "df",
        "cp", "tar", "zstd", "pv", "ionice", "nice", "su",
    ]

    def check_dependencies(self):
        """
        Verifica se todas as ferramentas externas necessárias estão disponíveis
        no PATH antes de iniciar qualquer operação. Encerra com erro listando
        claramente o que está faltando, para que o administrador possa instalar
        os pacotes necessários antes de tentar novamente.
        """
        missing = [tool for tool in self.REQUIRED_TOOLS if not shutil.which(tool)]
        if missing:
            self.logger.error(
                "Ferramentas obrigatórias não encontradas no PATH: %s. "
                "Instale os pacotes correspondentes e tente novamente.",
                ", ".join(missing)
            )
            sys.exit(1)
        self.logger.info(
            "Dependências OK: todas as ferramentas necessárias foram encontradas."
        )

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def check_pre_flight(self):
        # Dependências primeiro — aborta imediatamente se algo faltar
        self.check_dependencies()

        # FIX 1 — verifica permissões do arquivo de config
        self._check_config_file_permissions()

        req_mb    = self.config['settings']['min_space_mb']
        req_bytes = req_mb * 1024 * 1024

        check_path = self.config['paths']['backup_root']
        while not os.path.exists(check_path):
            check_path = os.path.dirname(check_path)
            if not check_path or check_path == "/":
                break
        if not os.path.exists(check_path):
            check_path = "/"

        total, used, free = shutil.disk_usage(check_path)
        if free < req_bytes:
            raise Exception(f"Espaço insuficiente. Livre: {free/1024/1024:.2f} MB")

        self.logger.info(f"Disco OK. Livre: {free/1024/1024:.2f} MB")

        for d in [self.orig_dir, self.incr_dir, self.diff_dir, self.full_dir]:
            if not os.path.exists(d):
                self.logger.info(f"Criando: {d}")
                os.makedirs(d, exist_ok=True)

    # ------------------------------------------------------------------
    # Montagem CIFS
    # ------------------------------------------------------------------

    def mount_share(self):
        if os.path.ismount(self.orig_dir):
            self.logger.info("Já montado. Pulando.")
            return False

        creds    = self.config.get('credentials', {})
        user     = creds.get('username')
        password = creds.get('password')
        domain   = creds.get('domain')

        remote = self.config['paths']['remote_share']
        opts   = self.config['settings'].get('mount_options', 'ro')

        if not user or not password:
            raise ValueError("Credenciais incompletas no JSON.")

        self.logger.info(f"Montando {remote}...")

        auth_opts  = f"username={user},password={password}"
        if domain:
            auth_opts += f",domain={domain}"
        final_opts = f"{opts},{auth_opts}"

        cmd = ["mount", "-t", "cifs", remote, self.orig_dir, "-o", final_opts]
        try:
            self._run_cmd(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            raise Exception(f"Erro no mount (Exit Code {e.returncode})")

    # ------------------------------------------------------------------
    # Rsync
    # ------------------------------------------------------------------

    def run_rsync(self):
        """
        FIX 3 — O rsync é executado sob o usuário configurado em 'rsync_user'
        utilizando 'su -c', replicando o comportamento do script shell original.

        GRACEFUL SHUTDOWN — Ao receber SIGINT (Ctrl+C), o sinal é encaminhado ao
        processo rsync filho e o script aguarda ele terminar antes de prosseguir
        com a desmontagem. Isso evita o 'Broken pipe' e garante que o rsync feche
        seus descritores e libere o mountpoint corretamente.
        """
        bw_kb       = int(self.config['settings']['bandwidth_limit_mb'] * 1024)
        rsync_user  = self.config['settings'].get('rsync_user', 'root')

        default_flags = [
            "-ahx", "--acls", "--xattrs", "--numeric-ids",
            "--chmod=ugo+r", "--ignore-errors", "--force", "--delete",
            "--info=del,name,stats2"
        ]
        rsync_flags = self.config['settings'].get('rsync_flags', default_flags)

        with tempfile.NamedTemporaryFile(mode='w', suffix='.excl', delete=False) as tmp:
            if 'excludes' in self.config:
                tmp.write('\n'.join(self.config['excludes']))
            tmp_exclude = tmp.name

        # Permissão de leitura para o usuário que executa o rsync
        os.chmod(tmp_exclude, 0o644)

        proc = None
        interrupted = False

        try:
            src = self.orig_dir if self.orig_dir.endswith('/') else self.orig_dir + '/'

            rsync_parts = [
                "rsync",
                f"--bwlimit={bw_kb}",
                *rsync_flags,
                f"--exclude-from={tmp_exclude}",
                "--backup",
                f"--backup-dir={self.diff_dir}",
                f"--log-file={self.log_file}",
                src,
                self.incr_dir
            ]

            # Monta o comando su -c "<cmd>" se o usuário for diferente do atual
            current_user = os.environ.get('USER') or os.environ.get('LOGNAME') or 'root'
            if rsync_user != current_user:
                cmd_str = ' '.join(
                    subprocess.list2cmdline([p]) if ' ' in p else p
                    for p in rsync_parts
                )
                cmd = ["su", "-", rsync_user, "-c", cmd_str]
                self.logger.info(f"Executando Rsync como usuário '{rsync_user}'...")
            else:
                cmd = rsync_parts
                self.logger.info("Executando Rsync...")

            if self.debug:
                self.logger.debug("CMD: " + ' '.join(str(x) for x in cmd))

            # Usa Popen para manter referência ao processo filho e poder
            # encaminhar sinais de forma controlada.
            proc = subprocess.Popen(cmd)

            # Captura SIGINT enquanto o rsync está rodando.
            # Em vez de lançar KeyboardInterrupt imediatamente, encaminha o sinal
            # ao filho e aguarda ele finalizar — evitando broken pipe e umount
            # com mountpoint ocupado.
            original_sigint = signal.getsignal(signal.SIGINT)

            def _handle_sigint(signum, frame):
                nonlocal interrupted
                interrupted = True
                self.logger.warning(
                    "Interrupção recebida (Ctrl+C). Aguardando rsync finalizar "
                    "graciosamente antes de desmontar..."
                )
                if proc and proc.poll() is None:
                    proc.send_signal(signal.SIGINT)  # Encaminha ao rsync filho

            signal.signal(signal.SIGINT, _handle_sigint)

            try:
                proc.wait()  # Bloqueia até o rsync terminar (inclusive após SIGINT)
            finally:
                # Restaura o handler original independente do que acontecer
                signal.signal(signal.SIGINT, original_sigint)

            returncode = proc.returncode

            if interrupted:
                # Rsync recebeu SIGINT e saiu com code 20 — saída esperada
                self.logger.warning(
                    f"Rsync interrompido pelo usuário (code {returncode}). "
                    "Prosseguindo para desmontagem segura."
                )
                # Propaga KeyboardInterrupt para o fluxo principal (finally do main
                # garante a desmontagem)
                raise KeyboardInterrupt
            elif returncode == 0:
                self.logger.info("Rsync: Sucesso.")
            elif returncode == 23:
                self.logger.warning(
                    "Rsync: Aviso (Code 23) — transferência parcial "
                    "(ex: permissão negada). Continuando."
                )
            else:
                raise subprocess.CalledProcessError(returncode, cmd)

        finally:
            if os.path.exists(tmp_exclude):
                os.remove(tmp_exclude)

    # ------------------------------------------------------------------
    # Limpeza diferencial
    # ------------------------------------------------------------------

    def cleanup_differential(self):
        policy = self.config['settings'].get('retention_policy', {})
        days   = policy.get('keep_differential_files_days', 240)

        self.logger.info(f"Limpando Diff > {days} dias...")
        self._run_cmd(
            ["find", self.diff_dir, "-type", "f", "-mtime", f"+{days}", "-delete"],
            check=False
        )

        if policy.get('cleanup_empty_dirs', True):
            self._run_cmd(
                ["find", self.diff_dir, "-type", "d", "-empty", "-delete"],
                check=False
            )

    # ------------------------------------------------------------------
    # Full Backup
    # ------------------------------------------------------------------

    def run_full_backup(self):
        policy    = self.config['settings'].get('retention_policy', {})
        retention = policy.get('keep_full_backups_days', 30)

        self.logger.info(f"Verificando Fulls antigos (> {retention} dias)...")
        self._run_cmd(
            ["find", self.full_dir, "-type", "f", "-name", "Full_*.tar.zst",
             "-mtime", f"+{retention}", "-delete"],
            check=False
        )

        # Verifica se existe um Full válido (dentro do período de retenção)
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
            self.logger.info(
                f"Backup Full válido encontrado ({recent_file}). Mantendo estrutura atual."
            )
            return

        # ------------------------------------------------------------------
        # FIX 8 — Verifica espaço dinamicamente com base no tamanho real do INCR_DIR
        # Replica: INCR_SIZE=$(du -s --block-size=1K "$INCR_DIR") + 10% de margem
        # ------------------------------------------------------------------
        self.logger.info("Calculando espaço necessário para o Full (INCR_DIR + 10%)...")
        du_res = self._run_cmd(
            ["du", "-s", "--block-size=1K", self.incr_dir],
            check=True, capture_output=True, text=True
        )
        incr_size_kb = int(du_res.stdout.split()[0])
        min_space_kb  = incr_size_kb + incr_size_kb // 10   # +10% de margem
        min_space_mb  = min_space_kb / 1024

        df_res = self._run_cmd(
            ["df", "--output=avail", self.full_dir],
            check=True, capture_output=True, text=True
        )
        avail_kb = int(df_res.stdout.strip().splitlines()[-1])

        self.logger.info(
            f"INCR_DIR: {incr_size_kb} KB | Necessário (+ 10%): {min_space_kb} KB "
            f"| Disponível em FULL_DIR: {avail_kb} KB"
        )

        if avail_kb < min_space_kb:
            raise Exception(
                f"Espaço insuficiente em {self.full_dir}. "
                f"Necessário: {min_space_mb:.1f} MB, "
                f"Disponível: {avail_kb/1024:.1f} MB"
            )

        # Reflink / cp do snapshot
        persistent_full_dir = os.path.join(self.full_dir, "Full")
        if os.path.exists(persistent_full_dir):
            self.logger.info(f"Removendo '{persistent_full_dir}' antigo para atualização...")
            shutil.rmtree(persistent_full_dir)

        self.logger.info("Criando novo Snapshot 'Full' (Reflink)...")
        try:
            self._run_cmd(
                ["cp", "-a", "--reflink=always", self.incr_dir, persistent_full_dir],
                check=True, stderr=subprocess.PIPE
            )
        except subprocess.CalledProcessError:
            self.logger.warning("Reflink falhou. Usando cp -a (cópia completa).")
            self._run_cmd(["cp", "-a", self.incr_dir, persistent_full_dir], check=True)

        # Compressão
        filename  = f"Full_{self.date_str}.tar.zst"
        zst_path  = os.path.join(self.full_dir, filename)

        ionice_class   = str(self.config['settings']['ionice_class'])
        nice_priority  = str(self.config['settings']['nice_priority'])
        transfer_rate  = self.config['settings'].get('transfer_rate_pv', '10m')

        self.logger.info(f"Compactando: {filename}")

        cmd_tar  = [
            "ionice", "-c", ionice_class,
            "nice", "-n", nice_priority,
            "tar", "-cvf", "-",
            "-C", self.full_dir,
            "Full"
        ]
        cmd_pv   = ["pv", "-q", "-L", transfer_rate]  # FIX 2 — restaura controle de taxa via pv
        cmd_zstd = ["zstd", "--threads=2"]

        if self.debug:
            self.logger.debug(
                f"Pipeline: {' '.join(cmd_tar)} | {' '.join(cmd_pv)} "
                f"| {' '.join(cmd_zstd)} > {zst_path}"
            )

        # FIX 4 — verifica exit code de TODOS os processos do pipeline (equivale a pipefail)
        try:
            p_tar  = subprocess.Popen(cmd_tar,  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p_pv   = subprocess.Popen(cmd_pv,   stdin=p_tar.stdout,  stdout=subprocess.PIPE)
            p_tar.stdout.close()   # Permite que p_tar receba SIGPIPE se p_pv sair

            with open(zst_path, "wb") as f_out:
                p_zstd = subprocess.Popen(cmd_zstd, stdin=p_pv.stdout, stdout=f_out)
            p_pv.stdout.close()    # Permite que p_pv receba SIGPIPE se p_zstd sair

            p_zstd.wait()
            p_pv.wait()
            p_tar.wait()

            errors = []
            if p_tar.returncode  != 0: errors.append(f"tar  (code {p_tar.returncode})")
            if p_pv.returncode   != 0: errors.append(f"pv   (code {p_pv.returncode})")
            if p_zstd.returncode != 0: errors.append(f"zstd (code {p_zstd.returncode})")

            if errors:
                raise Exception(f"Falha no pipeline de compressão: {', '.join(errors)}")

            zst_size_kb = int(
                self._run_cmd(
                    ["du", "-s", "--block-size=1K", zst_path],
                    check=True, capture_output=True, text=True
                ).stdout.split()[0]
            )
            self.logger.info(
                f"Full Backup concluído. Arquivo: {zst_path} ({zst_size_kb} KB). "
                f"Diretório 'Full' mantido no disco."
            )

        except Exception as e:
            # Remove arquivo parcial em caso de erro
            if os.path.exists(zst_path):
                os.remove(zst_path)
                self.logger.warning(f"Arquivo parcial '{zst_path}' removido.")
            self.logger.error(f"Erro na compactação: {e}")
            raise

    # ------------------------------------------------------------------
    # Limpeza de logs
    # FIX 5 — retenção por CONTAGEM (keep_logs_count) em vez de só por data,
    # replicando o `tail -n +32` do script shell original.
    # ------------------------------------------------------------------

    def cleanup_logs(self):
        policy    = self.config['settings'].get('retention_policy', {})
        keep_count = policy.get('keep_logs_count', 31)
        log_dir   = self.config['paths']['log_dir']

        log_pattern = f"backup_{self.safe_name}_*.log"
        self.logger.info(
            f"Limpando logs de '{self.safe_name}', mantendo os {keep_count} mais recentes..."
        )

        # Lista todos os logs correspondentes, ordenados do mais novo ao mais velho
        try:
            all_logs = sorted(
                [
                    os.path.join(log_dir, f)
                    for f in os.listdir(log_dir)
                    if f.startswith(f"backup_{self.safe_name}_") and f.endswith(".log")
                ],
                key=os.path.getmtime,
                reverse=True
            )
        except FileNotFoundError:
            self.logger.warning(f"Diretório de log '{log_dir}' não encontrado. Pulando limpeza.")
            return

        logs_to_delete = all_logs[keep_count:]
        for log_path in logs_to_delete:
            try:
                os.remove(log_path)
                self.logger.info(f"Log removido: {log_path}")
            except OSError as e:
                self.logger.warning(f"Não foi possível remover '{log_path}': {e}")

    # ------------------------------------------------------------------
    # Cleanup final
    # ------------------------------------------------------------------

    def cleanup(self, did_mount):
        if did_mount:
            self.logger.info("Desmontando...")
            self._run_cmd(["umount", self.orig_dir], check=False)


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Gerenciador de Backup Corporativo",
        epilog=HELP_TEXT,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("config_file", nargs="?",  help="Caminho do arquivo JSON de configuração")
    parser.add_argument("--init",      action="store_true", help="Cria um modelo de configuração padrão")
    parser.add_argument("--debug",     action="store_true", help="Exibe os comandos executados para depuração")
    args = parser.parse_args()

    if args.init:
        target = args.config_file if args.config_file else "config_modelo.json"
        if os.path.exists(target):
            print(f"[\033[93mAVISO\033[0m] '{target}' já existe. Abortando para não sobrescrever.")
            sys.exit(1)
        with open(target, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        os.chmod(target, 0o600)   # Proteção imediata das credenciais
        print(f"Modelo criado: {target} (chmod 600 aplicado)")
        sys.exit(0)

    if not args.config_file:
        parser.print_help()
        sys.exit(1)

    if not os.path.exists(args.config_file):
        print(f"[\033[93mAVISO\033[0m] '{args.config_file}' não encontrado.")
        print("Criando modelo padrão...")
        with open(args.config_file, 'w') as f:
            json.dump(DEFAULT_JSON_CONFIG, f, indent=4)
        os.chmod(args.config_file, 0o600)
        print("Arquivo criado (chmod 600). Edite-o e tente novamente.")
        sys.exit(0)

    job = None
    did_mount = False
    try:
        job = BackupJob(args.config_file, debug=args.debug)
        job.logger.info(f"=== Job Iniciado: {args.config_file} ===")
        if args.debug:
            job.logger.info("MODO DEBUG ATIVADO")

        job.check_pre_flight()
        did_mount = job.mount_share()

        job.run_rsync()
        job.cleanup_differential()
        job.run_full_backup()
        job.cleanup_logs()

        job.logger.info("=== Sucesso ===")

    except KeyboardInterrupt:
        if job:
            job.logger.warning("=== Job interrompido pelo usuário (Ctrl+C) ===")
        else:
            print("\n[AVISO] Interrompido pelo usuário.")
        sys.exit(130)
    except Exception as e:
        if job:
            job.logger.error(f"FALHA: {e}")
        else:
            print(f"ERRO: {e}")
        sys.exit(1)
    finally:
        if job:
            job.cleanup(did_mount)


if __name__ == "__main__":
    main()