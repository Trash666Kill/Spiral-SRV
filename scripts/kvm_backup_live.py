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

# --- VARIÁVEIS GLOBAIS DE ESTADO (PARA LIMPEZA) ---
# Necessárias para que o Signal Handler saiba o que limpar
CURRENT_DOMAIN_OBJ = None
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
        print(f"ERRO CRÍTICO LOGS: {e}", file=sys.stderr)
        sys.exit(1)

# --- FUNÇÕES DE LIMPEZA E SINAIS ---

def perform_cleanup(exit_after=False):
    """Executa a limpeza de arquivos parciais e jobs travados."""
    global BACKUP_JOB_RUNNING, CURRENT_SNAPSHOT_NAME
    
    print("\n") # Pular linha visualmente
    logger.warning("--- INICIANDO PROTOCOLO DE LIMPEZA ---")

    # 1. Abortar Job do Libvirt (se ativo)
    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        logger.info(f"Tentando abortar job no domínio '{CURRENT_DOMAIN_NAME}'...")
        try:
            subprocess.run(['virsh', 'domjobabort', CURRENT_DOMAIN_NAME], capture_output=True)
            logger.info("  -> Job abortado.")
        except Exception as e:
            logger.error(f"  -> Falha ao abortar job: {e}")
        BACKUP_JOB_RUNNING = False

    # 2. Remover Snapshot Temporário (se existir)
    if CURRENT_SNAPSHOT_NAME and CURRENT_DOMAIN_NAME:
        logger.info(f"Removendo snapshot órfão '{CURRENT_SNAPSHOT_NAME}'...")
        try:
            subprocess.run(
                ['virsh', 'snapshot-delete', CURRENT_DOMAIN_NAME, CURRENT_SNAPSHOT_NAME, '--metadata'], 
                capture_output=True
            )
            logger.info("  -> Snapshot removido.")
        except Exception as e:
            logger.error(f"  -> Falha ao remover snapshot: {e}")
        CURRENT_SNAPSHOT_NAME = None

    # 3. Deletar Arquivos Parciais (.bak ou .qcow2 temporários)
    if FILES_TO_CLEANUP:
        logger.info("Removendo arquivos de backup incompletos...")
        for fpath in FILES_TO_CLEANUP:
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                    logger.info(f"  -> Deletado: {os.path.basename(fpath)}")
                except Exception as e:
                    logger.error(f"  -> Erro ao deletar {fpath}: {e}")
            else:
                # Pode já ter sido movido ou deletado
                pass
    
    logger.warning("--- LIMPEZA CONCLUÍDA ---")
    if exit_after:
        sys.exit(1)

def signal_handler(sig, frame):
    """Captura Ctrl+C (SIGINT) e força limpeza."""
    logger.warning(f"\nSINAL DE INTERRUPÇÃO DETECTADO ({sig})!")
    perform_cleanup(exit_after=True)

# Registra o listener de Ctrl+C
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
                    logger.info(f"Retenção - Removido antigo: {f}")
                except: pass

def check_available_space(backup_dir, disk_details):
    total_needed = sum([os.path.getsize(i['path']) for i in disk_details.values()]) * (1 + SAFETY_MARGIN_PERCENT)
    os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
    free = shutil.disk_usage(os.path.dirname(backup_dir)).free
    if total_needed > free:
        logger.error(f"Espaço insuficiente. Precisa: {total_needed/1e9:.2f}GB, Livre: {free/1e9:.2f}GB")
        return False
    return True

# --- MODOS DE BACKUP ---

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp):
    global BACKUP_JOB_RUNNING, FILES_TO_CLEANUP
    
    backup_xml_parts = ["<domainbackup><disks>"]
    
    for target_dev, info in disk_details.items():
        fpath = os.path.join(backup_dir, f"{dom.name()}-{target_dev}-{timestamp}.{DISK_FORMAT}.bak")
        # REGISTRA O ARQUIVO PARA LIMPEZA EM CASO DE ERRO
        FILES_TO_CLEANUP.append(fpath)
        
        backup_xml_parts.append(f"<disk name='{target_dev}' type='file'><target file='{fpath}'/><driver type='{DISK_FORMAT}'/></disk>")
        logger.info(f"  -> Alvo: {fpath}")

    backup_xml = "".join(backup_xml_parts) + "</disks></domainbackup>"
    
    logger.info("[Libvirt] Iniciando Job...")
    start_time = time.time()
    
    try:
        dom.backupBegin(backup_xml, None, 0)
        BACKUP_JOB_RUNNING = True # Marca job como ativo
        
        spinner = "|/-\\"
        last_log = 0
        
        while True:
            stats = dom.jobStats()
            if not stats or stats.get('type', libvirt.VIR_DOMAIN_JOB_NONE) == libvirt.VIR_DOMAIN_JOB_NONE:
                if sys.stdout.isatty(): print()
                logger.info("Sucesso! Backup finalizado.")
                BACKUP_JOB_RUNNING = False # Job acabou, desmarca flag
                FILES_TO_CLEANUP.clear() # Sucesso, remove da lista de limpeza (não deletar os bons!)
                break
            
            processed = stats.get('data_processed', 0) / 1024**2
            elapsed = time.time() - start_time
            spin = spinner[int(time.time()*4) % 4]
            
            msg = f"[{spin}] Copiando: {processed:.0f} MB... ({elapsed:.0f}s)"
            
            if sys.stdout.isatty():
                sys.stdout.write(f"\rINFO: {msg}".ljust(60))
                sys.stdout.flush()
            elif time.time() - last_log > 60:
                logger.info(msg)
                last_log = time.time()
            
            time.sleep(0.25)
            
    except Exception as e:
        logger.error(f"Erro durante execução Libvirt: {e}")
        perform_cleanup() # Chama limpeza explicita
        raise

def run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit):
    global CURRENT_SNAPSHOT_NAME, FILES_TO_CLEANUP
    
    snap_name = f"{dom.name()}_snap_{timestamp}"
    CURRENT_SNAPSHOT_NAME = snap_name
    
    # Preparar comandos
    cmd_snap = ['virsh', 'snapshot-create-as', '--domain', dom.name(), '--name', snap_name, '--disk-only', '--atomic', '--no-metadata']
    cmds_rsync = []
    cmds_pivot = []
    
    # Montar estrutura
    for dev, info in disk_details.items():
        base_dir = os.path.dirname(info['path'])
        snap_file = os.path.join(base_dir, f"{dom.name()}_{dev}_tmp_{timestamp}.qcow2")
        dest_file = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.bak")
        
        # Registra arquivos que serão criados
        FILES_TO_CLEANUP.append(dest_file) 
        
        cmd_snap.extend(['--diskspec', f"{dev},file={snap_file},driver=qcow2"])
        
        rsync = ['rsync', '-ah', '--inplace', '--progress', info['path'], dest_file]
        if bwlimit > 0: rsync.insert(1, f"--bwlimit={bwlimit*1024}")
        cmds_rsync.append(rsync)
        
        cmds_pivot.append(['virsh', 'blockcommit', dom.name(), dev, '--active', '--pivot'])

    try:
        logger.info("[Snapshot] Criando snapshot...")
        run_subprocess(cmd_snap)
        
        logger.info("[Snapshot] Iniciando Rsync...")
        for cmd in cmds_rsync:
            run_subprocess(cmd)
            
        logger.info("[Snapshot] Fazendo Pivot (Blockcommit)...")
        for cmd in cmds_pivot:
            run_subprocess(cmd)
            
        CURRENT_SNAPSHOT_NAME = None # Já foi comitado, não precisa mais limpar snapshot
        FILES_TO_CLEANUP.clear() # Sucesso, não deletar backups
        logger.info("Sucesso! Modo Snapshot finalizado.")
        
    except Exception as e:
        logger.error(f"Erro no fluxo Snapshot: {e}")
        perform_cleanup()
        raise

# --- MAIN ---

def run_backup(args, timestamp):
    global CURRENT_DOMAIN_NAME, CURRENT_DOMAIN_OBJ
    
    CURRENT_DOMAIN_NAME = args.domain
    conn = None
    
    try:
        conn = libvirt.open(CONNECT_URI)
        if not conn: raise Exception("Falha conexão Libvirt")
        
        dom = conn.lookupByName(args.domain)
        CURRENT_DOMAIN_OBJ = dom
        
        # Aborta jobs presos anteriores
        if dom.jobInfo()[0] != libvirt.VIR_DOMAIN_JOB_NONE:
            subprocess.run(['virsh', 'domjobabort', args.domain], stderr=subprocess.DEVNULL)
            
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
        # A limpeza já é chamada dentro das subfunções ou pelo signal handler
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