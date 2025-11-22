#!/usr/bin/env python3
import libvirt
import sys
import os
import shutil
import time
import argparse
import subprocess
import signal
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import logging

# --- CONSTANTES ---
DISK_FORMAT = 'qcow2'
CONNECT_URI = 'qemu:///system'
SAFETY_MARGIN_PERCENT = 0.10
LOG_DIR = "/var/log/virsh"

# --- VARIÁVEIS GLOBAIS (Estado) ---
CURRENT_DOMAIN_NAME = None
CURRENT_SNAPSHOT_NAME = None
FILES_TO_CLEANUP = []
BACKUP_JOB_RUNNING = False

# --- Configuração do Logger ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG) 

def setup_logging(domain_name, timestamp):
    try:
        log_dir_final = LOG_DIR
        if not os.access(os.path.dirname(LOG_DIR), os.W_OK) and not os.path.isdir(LOG_DIR):
            log_dir_final = "/tmp/virsh_logs"
            
        os.makedirs(log_dir_final, exist_ok=True)
        log_filename = f"{domain_name}-{timestamp}.log"
        log_path = os.path.join(log_dir_final, log_filename)
        
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)
        
    except Exception as e:
        print(f"ERRO LOGS: {e}", file=sys.stderr)
        sys.exit(1)

# --- FUNÇÕES DE LIMPEZA ---

def perform_cleanup(exit_after=False):
    """Limpa arquivos parciais e aborta jobs se interrompido."""
    global BACKUP_JOB_RUNNING, CURRENT_SNAPSHOT_NAME
    
    if sys.stdout.isatty(): print() 
    logger.warning("--- LIMPEZA INICIADA ---")

    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        logger.info(f"Abortando job '{CURRENT_DOMAIN_NAME}'...")
        try:
            subprocess.run(['virsh', 'domjobabort', CURRENT_DOMAIN_NAME], capture_output=True)
        except Exception: pass
        BACKUP_JOB_RUNNING = False

    if CURRENT_SNAPSHOT_NAME and CURRENT_DOMAIN_NAME:
        logger.info(f"Removendo snapshot '{CURRENT_SNAPSHOT_NAME}'...")
        try:
            subprocess.run(['virsh', 'snapshot-delete', CURRENT_DOMAIN_NAME, CURRENT_SNAPSHOT_NAME, '--metadata'], capture_output=True)
        except Exception: pass
        CURRENT_SNAPSHOT_NAME = None

    if FILES_TO_CLEANUP:
        logger.info("Removendo arquivos incompletos...")
        for fpath in FILES_TO_CLEANUP:
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    logger.info(f"  -> Deletado: {os.path.basename(fpath)}")
                except Exception: pass
    
    if exit_after:
        logger.warning("--- INTERROMPIDO PELO USUÁRIO ---")
        sys.exit(1)

def signal_handler(sig, frame):
    perform_cleanup(exit_after=True)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- UTILS ---

def run_subprocess(command_list):
    try:
        logger.info(f"CMD: {' '.join(command_list)}")
        result = subprocess.run(command_list, check=True, capture_output=True, text=True)
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Erro CMD: {e.stderr}")
        raise

def get_disk_details_from_xml(dom, target_devs_list):
    logger.info(f"Lendo XML para discos: {target_devs_list}")
    details = {}
    try:
        root = ET.fromstring(dom.XMLDesc(0))
        for device in root.findall('./devices/disk'):
            target = device.find('target')
            if target is not None and target.get('dev') in target_devs_list:
                source = device.find('source')
                if source is not None and source.get('file'):
                    details[target.get('dev')] = {'path': source.get('file')}
    except Exception:
        return None
    return details

def manage_retention(backup_dir, retention_days):
    if not os.path.isdir(backup_dir): return
    cutoff = datetime.now() - timedelta(days=retention_days)
    for f in os.listdir(backup_dir):
        full_path = os.path.join(backup_dir, f)
        if f.endswith('.bak') and os.path.isfile(full_path):
            if datetime.fromtimestamp(os.path.getmtime(full_path)) < cutoff:
                try:
                    os.remove(full_path)
                    logger.info(f"Retenção - Removido: {f}")
                except: pass

def check_available_space(backup_dir, disk_details):
    total_needed = sum([os.path.getsize(i['path']) for i in disk_details.values()]) * (1 + SAFETY_MARGIN_PERCENT)
    os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
    free = shutil.disk_usage(os.path.dirname(backup_dir)).free
    if total_needed > free:
        logger.error(f"Espaço insuficiente. Precisa: {total_needed/1e9:.2f}GB, Livre: {free/1e9:.2f}GB")
        return False
    return True

def format_bytes(size):
    # Retorna em GB se > 1GB, senão MB
    power = 2**30 # 1024**3
    n = size / power
    if n >= 1: return f"{n:.2f} GB"
    return f"{size / (2**20):.2f} MB"

# --- MODO LIBVIRT (MODIFICADO PARA LER ARQUIVO) ---

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp):
    global BACKUP_JOB_RUNNING, FILES_TO_CLEANUP
    
    # 1. Calcula tamanho total da ORIGEM
    source_total_bytes = 0
    try:
        for info in disk_details.values():
            source_total_bytes += os.path.getsize(info['path'])
    except Exception:
        source_total_bytes = 1 # Evita div/0

    backup_xml_parts = ["<domainbackup><disks>"]
    
    for target_dev, info in disk_details.items():
        fpath = os.path.join(backup_dir, f"{dom.name()}-{target_dev}-{timestamp}.{DISK_FORMAT}.bak")
        FILES_TO_CLEANUP.append(fpath)
        backup_xml_parts.append(f"<disk name='{target_dev}' type='file'><target file='{fpath}'/><driver type='{DISK_FORMAT}'/></disk>")
        logger.info(f"  -> Destino: {fpath}")

    backup_xml = "".join(backup_xml_parts) + "</disks></domainbackup>"
    
    logger.info("[Libvirt] Iniciando Job...")
    start_time = time.time()
    
    try:
        dom.backupBegin(backup_xml, None, 0)
        BACKUP_JOB_RUNNING = True
        
        spinner = "|/-\\"
        last_log = 0
        
        while True:
            stats = dom.jobStats()
            if not stats or stats.get('type', libvirt.VIR_DOMAIN_JOB_NONE) == libvirt.VIR_DOMAIN_JOB_NONE:
                if sys.stdout.isatty(): print()
                logger.info("Sucesso! Backup finalizado.")
                BACKUP_JOB_RUNNING = False
                FILES_TO_CLEANUP.clear()
                break
            
            # --- LÓGICA DE TAMANHO REAL ---
            # Soma o tamanho atual dos arquivos de DESTINO no disco
            dest_current_bytes = 0
            for fpath in FILES_TO_CLEANUP:
                try:
                    if os.path.exists(fpath):
                        dest_current_bytes += os.path.getsize(fpath)
                except OSError: pass
            
            percent = (dest_current_bytes / source_total_bytes * 100) if source_total_bytes > 0 else 0
            elapsed = time.time() - start_time
            spin = spinner[int(time.time()*4) % 4]
            
            # Formata para ficar legível, mas mostra os bytes se preferir
            # Opção 1: Bytes brutos (como ls -al)
            # msg = f"[{spin}] {dest_current_bytes} / {source_total_bytes} Bytes ({percent:.1f}%)"
            
            # Opção 2: GB/MB (Mais legível)
            msg = f"[{spin}] Tamanho: {dest_current_bytes} / {source_total_bytes} Bytes ({percent:.1f}%)"
            
            if sys.stdout.isatty():
                sys.stdout.write(f"\rINFO: {msg}".ljust(80))
                sys.stdout.flush()
            elif time.time() - last_log > 60:
                logger.info(msg)
                last_log = time.time()
            
            time.sleep(0.5) # Intervalo um pouco maior para leitura de disco
            
    except Exception as e:
        logger.error(f"Erro Libvirt: {e}")
        perform_cleanup()
        raise

# --- MODO SNAPSHOT ---

def run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit):
    global CURRENT_SNAPSHOT_NAME, FILES_TO_CLEANUP
    
    # Neste modo, o rsync já mostra o progresso detalhado se rodar interativo
    # Mas vamos manter a estrutura
    snap_name = f"{dom.name()}_snap_{timestamp}"
    CURRENT_SNAPSHOT_NAME = snap_name
    
    cmd_snap = ['virsh', 'snapshot-create-as', '--domain', dom.name(), '--name', snap_name, '--disk-only', '--atomic', '--no-metadata']
    cmds_rsync = []
    cmds_pivot = []
    
    for dev, info in disk_details.items():
        base_dir = os.path.dirname(info['path'])
        snap_file = os.path.join(base_dir, f"{dom.name()}_{dev}_tmp_{timestamp}.qcow2")
        dest_file = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.bak")
        
        FILES_TO_CLEANUP.append(dest_file) 
        
        cmd_snap.extend(['--diskspec', f"{dev},file={snap_file},driver=qcow2"])
        
        # Rsync com progress nativo
        rsync = ['rsync', '-ah', '--inplace', '--progress', info['path'], dest_file]
        if bwlimit > 0: rsync.insert(1, f"--bwlimit={bwlimit*1024}")
        cmds_rsync.append(rsync)
        
        cmds_pivot.append(['virsh', 'blockcommit', dom.name(), dev, '--active', '--pivot'])

    try:
        logger.info("[Snapshot] Criando snapshot...")
        run_subprocess(cmd_snap)
        
        logger.info("[Snapshot] Iniciando Rsync (Acompanhe saída abaixo)...")
        for cmd in cmds_rsync:
            # subprocess.run conecta stdout direto no terminal para ver o rsync
            subprocess.run(cmd, check=True)
            
        logger.info("[Snapshot] Fazendo Pivot...")
        for cmd in cmds_pivot:
            run_subprocess(cmd)
            
        CURRENT_SNAPSHOT_NAME = None
        FILES_TO_CLEANUP.clear()
        logger.info("Sucesso! Modo Snapshot finalizado.")
        
    except Exception as e:
        logger.error(f"Erro Snapshot: {e}")
        perform_cleanup()
        raise

# --- MAIN ---

def run_backup(args, timestamp):
    global CURRENT_DOMAIN_NAME
    CURRENT_DOMAIN_NAME = args.domain
    conn = None
    
    try:
        conn = libvirt.open(CONNECT_URI)
        if not conn: raise Exception("Falha conexão Libvirt")
        
        dom = conn.lookupByName(args.domain)
        
        # Limpa jobs velhos
        try:
            if dom.jobInfo()[0] != libvirt.VIR_DOMAIN_JOB_NONE:
                subprocess.run(['virsh', 'domjobabort', args.domain], stderr=subprocess.DEVNULL)
        except: pass
            
        backup_dir = os.path.join(args.backup_dir, args.domain)
        manage_retention(backup_dir, args.retention_days)
        
        disk_details = get_disk_details_from_xml(dom, args.disk)
        if not disk_details: raise Exception("Discos não encontrados")
        
        if not check_available_space(backup_dir, disk_details):
            sys.exit(1)
            
        if args.mode == 'libvirt':
            run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp)
        else:
            run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, args.bwlimit)

    except Exception as e:
        logger.exception(f"Falha geral: {e}")
        sys.exit(1)
    finally:
        if conn: conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True)
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--disk', required=True, nargs='+')
    parser.add_argument('--retention-days', type=int, default=7)
    parser.add_argument('--mode', choices=['libvirt', 'snapshot'], default='libvirt')
    parser.add_argument('--bwlimit', type=int, default=0)
    
    args = parser.parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    setup_logging(args.domain, timestamp)
    run_backup(args, timestamp)