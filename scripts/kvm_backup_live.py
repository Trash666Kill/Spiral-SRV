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
FORBIDDEN_PATTERNS = ['_snap_', '_tmp_', 'snapshot', '.bak']

# --- VARIÁVEIS GLOBAIS ---
CURRENT_DOMAIN_NAME = None
CURRENT_SNAPSHOT_NAME = None
FILES_TO_CLEANUP = []      # Arquivos de DESTINO (.bak)
TEMP_SOURCE_FILES = []     # Arquivos de ORIGEM temporários (_tmp_)
CURRENT_COPY_PROCESS = None # Processo CP/Rsync ativo
BACKUP_JOB_RUNNING = False

# --- LOGGER ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG) 

def setup_logging(domain_name, timestamp):
    try:
        log_dir_final = LOG_DIR
        if not os.access(os.path.dirname(LOG_DIR), os.W_OK) and not os.path.isdir(LOG_DIR):
            log_dir_final = "/tmp/virsh_logs"
        os.makedirs(log_dir_final, exist_ok=True)
        log_path = os.path.join(log_dir_final, f"{domain_name}-{timestamp}.log")
        
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter('%(levelname)s: %(message)s'))
        logger.addHandler(console_handler)
    except Exception as e:
        print(f"ERRO LOGS: {e}", file=sys.stderr); sys.exit(1)

# --- LIMPEZA E EMERGÊNCIA ---

def remove_temp_source_file(dev_name):
    global TEMP_SOURCE_FILES
    for f in list(TEMP_SOURCE_FILES):
        if f"_{dev_name}_" in os.path.basename(f):
            if os.path.exists(f):
                try: os.remove(f); logger.info(f" -> Arquivo temporário removido: {os.path.basename(f)}")
                except: pass
            if f in TEMP_SOURCE_FILES: TEMP_SOURCE_FILES.remove(f)

def get_dirty_disks_from_live_vm(domain_name):
    dirty_devs = []
    try:
        res = subprocess.run(['virsh', 'domblklist', domain_name], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    if any(p in os.path.basename(parts[1]) for p in FORBIDDEN_PATTERNS):
                        dirty_devs.append(parts[0])
    except: pass
    return dirty_devs

def perform_cleanup(exit_after=False):
    global BACKUP_JOB_RUNNING, CURRENT_SNAPSHOT_NAME, CURRENT_DOMAIN_NAME, CURRENT_COPY_PROCESS
    
    if sys.stdout.isatty(): print() 
    logger.warning("--- PROTOCOLO DE LIMPEZA INICIADO ---")

    # 0. Matar processo de cópia (cp) se estiver rodando
    if CURRENT_COPY_PROCESS and CURRENT_COPY_PROCESS.poll() is None:
        logger.warning("Encerrando processo de cópia ativo...")
        CURRENT_COPY_PROCESS.terminate()
        try: CURRENT_COPY_PROCESS.wait(timeout=2)
        except: CURRENT_COPY_PROCESS.kill()

    # 1. AUTO-CURA: Pivot de Emergência
    if CURRENT_DOMAIN_NAME:
        dirty_disks = get_dirty_disks_from_live_vm(CURRENT_DOMAIN_NAME)
        if dirty_disks:
            logger.warning("!"*60)
            logger.warning(f"ALERTA: VM EM ESTADO SUJO: {dirty_disks}")
            logger.warning("EXECUTANDO PIVOT DE EMERGÊNCIA...")
            logger.warning("!"*60)
            
            for dev in dirty_disks:
                try:
                    subprocess.run(['virsh', 'blockcommit', CURRENT_DOMAIN_NAME, dev, '--active', '--pivot'], check=True)
                    logger.info(f" -> SUCESSO: '{dev}' recuperado.")
                    remove_temp_source_file(dev)
                except:
                    logger.critical(f" -> FALHA: Execute manualmente: virsh blockcommit {CURRENT_DOMAIN_NAME} {dev} --active --pivot")

    # 2. Abortar Job Libvirt
    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        try: subprocess.run(['virsh', 'domjobabort', CURRENT_DOMAIN_NAME], capture_output=True)
        except: pass
        BACKUP_JOB_RUNNING = False

    # 3. Remover Metadados Snapshot
    if CURRENT_SNAPSHOT_NAME and CURRENT_DOMAIN_NAME:
        try: subprocess.run(['virsh', 'snapshot-delete', CURRENT_DOMAIN_NAME, CURRENT_SNAPSHOT_NAME, '--metadata'], capture_output=True)
        except: pass
        CURRENT_SNAPSHOT_NAME = None

    # 4. Limpar arquivos .bak
    if FILES_TO_CLEANUP:
        logger.info("Limpando arquivos parciais de destino...")
        for f in FILES_TO_CLEANUP:
            if os.path.exists(f):
                try: os.remove(f); logger.info(f" -> Deletado: {os.path.basename(f)}")
                except: pass
    
    if exit_after: logger.warning("--- INTERROMPIDO ---"); sys.exit(1)

def signal_handler(sig, frame):
    perform_cleanup(exit_after=True)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# --- UTILS ---

def run_subprocess(command_list, allow_fail=False):
    try:
        logger.info(f"CMD: {' '.join(command_list)}")
        return subprocess.run(command_list, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        if not allow_fail:
            logger.error(f"Erro CMD: {e.stderr}"); raise
        else: raise

def check_agent_availability(dom):
    try: dom.qemuAgentCommand('{"execute":"guest-ping"}', 1, 0); return True
    except: return False

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
    except Exception as e: logger.error(f"Erro XML: {e}"); return None
    return details

def check_clean_state(dom, disk_details):
    try:
        if dom.snapshotNum(0) > 0: return False, "Snapshot registrado detectado."
    except: pass
    for dev, info in disk_details.items():
        if any(p in os.path.basename(info['path']) for p in FORBIDDEN_PATTERNS):
            return False, f"Disco '{dev}' está sujo."
    return True, "Limpo"

def check_available_space(backup_dir, disk_details):
    needed = sum([os.path.getsize(i['path']) for i in disk_details.values()]) * (1 + SAFETY_MARGIN_PERCENT)
    os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
    if needed > shutil.disk_usage(os.path.dirname(backup_dir)).free:
        logger.error("Espaço insuficiente."); return False
    return True

# --- [NOVO] GERENCIAMENTO DE RETENÇÃO DETALHADO ---
def manage_retention(backup_dir, days):
    if not os.path.isdir(backup_dir): return
    
    cutoff = datetime.now() - timedelta(days=days)
    valid_files = []
    expired_files = []
    
    # Lista e classifica os arquivos
    try:
        # Ordena para ficar cronológico no log
        files = sorted(os.listdir(backup_dir))
        for f in files:
            fp = os.path.join(backup_dir, f)
            if f.endswith('.bak') and os.path.isfile(fp):
                mtime = datetime.fromtimestamp(os.path.getmtime(fp))
                if mtime < cutoff:
                    expired_files.append((fp, mtime))
                else:
                    valid_files.append((fp, mtime))
    except Exception as e:
        logger.error(f"Erro ao listar diretório para retenção: {e}")
        return

    # Exibe Relatório
    if sys.stdout.isatty(): print()
    logger.info(f"--- ANÁLISE DE RETENÇÃO ({days} dias) ---")
    
    # 1. Mostra Válidos
    if valid_files:
        logger.info("✅ VÁLIDOS (Mantidos):")
        for fp, mtime in valid_files:
            logger.info(f"   -> {os.path.basename(fp)} ({mtime.strftime('%d/%m/%Y %H:%M')})")
    else:
        logger.info("ℹ️  VÁLIDOS: Nenhum encontrado.")

    # 2. Mostra e Remove Expirados
    if expired_files:
        logger.info("❌ EXPIRADOS (Serão Removidos):")
        for fp, mtime in expired_files:
            logger.info(f"   -> {os.path.basename(fp)} ({mtime.strftime('%d/%m/%Y %H:%M')})")
            try:
                os.remove(fp)
                logger.info("      -> [OK] Arquivo removido.")
            except Exception as e:
                logger.error(f"      -> [ERRO] Falha ao remover: {e}")
    else:
        logger.info("ℹ️  EXPIRADOS: Nenhum arquivo antigo para limpar.")
    
    logger.info("-" * 40)
    if sys.stdout.isatty(): print()

# --- BACKUP MODES ---

def monitor_progress(source_total_bytes):
    spinner = "|/-\\"
    last_log = 0
    curr = 0
    for f in FILES_TO_CLEANUP:
        try:
            if os.path.exists(f): curr += os.path.getsize(f)
        except: pass
    
    perc = (curr / source_total_bytes * 100) if source_total_bytes > 0 else 0
    msg = f"[{spinner[int(time.time()*4)%4]}] Tamanho: {curr} / {source_total_bytes} Bytes ({perc:.1f}%)"
    
    if sys.stdout.isatty():
        sys.stdout.write(f"\rINFO: {msg}".ljust(80))
        sys.stdout.flush()
    elif time.time() - last_log > 60:
        logger.info(msg)
        return time.time()
    return last_log

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp):
    global BACKUP_JOB_RUNNING, FILES_TO_CLEANUP
    total_bytes = sum([os.path.getsize(i['path']) for i in disk_details.values()])
    
    xml = "<domainbackup><disks>"
    for dev, info in disk_details.items():
        fp = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.{DISK_FORMAT}.bak")
        FILES_TO_CLEANUP.append(fp)
        xml += f"<disk name='{dev}' type='file'><target file='{fp}'/><driver type='{DISK_FORMAT}'/></disk>"
        logger.info(f" -> Destino: {fp}")
    xml += "</disks></domainbackup>"

    logger.info("[Libvirt] Iniciando Job...")
    try:
        dom.backupBegin(xml, None, 0)
        BACKUP_JOB_RUNNING = True
        last_log_time = 0
        
        while True:
            stats = dom.jobStats()
            if not stats or stats.get('type', 0) == 0:
                print(); logger.info("Sucesso!"); BACKUP_JOB_RUNNING = False; FILES_TO_CLEANUP = []; break
            new_log_time = monitor_progress(total_bytes)
            if new_log_time: last_log_time = new_log_time
            time.sleep(0.5)
            
    except Exception as e:
        logger.error(f"Erro Libvirt: {e}"); perform_cleanup(); raise

def run_backup_snapshot_cp(dom, backup_dir, disk_details, timestamp):
    global CURRENT_SNAPSHOT_NAME, FILES_TO_CLEANUP, TEMP_SOURCE_FILES, CURRENT_COPY_PROCESS
    
    total_bytes = sum([os.path.getsize(i['path']) for i in disk_details.values()])
    snap_name = f"{dom.name()}_snap_{timestamp}"
    CURRENT_SNAPSHOT_NAME = snap_name
    
    cmd_base = ['virsh', 'snapshot-create-as', '--domain', dom.name(), '--name', snap_name, '--disk-only', '--atomic', '--no-metadata']
    
    copy_tasks = []
    
    for dev, info in disk_details.items():
        tmp = os.path.join(os.path.dirname(info['path']), f"{dom.name()}_{dev}_tmp_{timestamp}.qcow2")
        TEMP_SOURCE_FILES.append(tmp)
        
        dst = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.{DISK_FORMAT}.bak")
        FILES_TO_CLEANUP.append(dst)
        
        cmd_base.extend(['--diskspec', f"{dev},file={tmp},driver=qcow2"])
        cp_cmd = ['cp', '--archive', '--sparse=always', info['path'], dst]
        copy_tasks.append(cp_cmd)

    try:
        logger.info("[Snapshot] Criando snapshot...")
        use_quiesce = False
        if check_agent_availability(dom):
            try: run_subprocess(cmd_base + ['--quiesce'], allow_fail=True); use_quiesce = True
            except: pass
        if not use_quiesce:
            run_subprocess(cmd_base); logger.info(" -> Snapshot padrão criado.")

        logger.info("[Snapshot] Iniciando Cópia (CP)...")
        last_log_time = 0
        
        for cmd in copy_tasks:
            CURRENT_COPY_PROCESS = subprocess.Popen(cmd)
            while CURRENT_COPY_PROCESS.poll() is None:
                new_log_time = monitor_progress(total_bytes)
                if new_log_time: last_log_time = new_log_time
                time.sleep(0.5)
            
            if CURRENT_COPY_PROCESS.returncode != 0:
                raise Exception(f"Comando CP falhou com código {CURRENT_COPY_PROCESS.returncode}")
            CURRENT_COPY_PROCESS = None
            
        logger.info("\n[Snapshot] Fazendo Pivot...")
        for dev in disk_details.keys():
            subprocess.run(['virsh', 'blockcommit', dom.name(), dev, '--active', '--pivot'], check=True)
            remove_temp_source_file(dev)
            
        CURRENT_SNAPSHOT_NAME = None; FILES_TO_CLEANUP = []; logger.info("Sucesso!")
        
    except Exception as e:
        logger.error(f"Erro Snapshot: {e}"); perform_cleanup(); raise

# --- MAIN ---
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--domain', required=True)
    parser.add_argument('--backup-dir', required=True)
    parser.add_argument('--disk', required=True, nargs='+')
    parser.add_argument('--retention-days', type=int, default=7)
    parser.add_argument('--mode', choices=['libvirt', 'snapshot'], default='libvirt')
    parser.add_argument('--bwlimit', type=int, default=0)
    parser.add_argument('--force-unsafe', action='store_true')
    args = parser.parse_args()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    setup_logging(args.domain, timestamp)
    
    if args.mode == 'snapshot' and not args.force_unsafe:
        logger.warning("\n!!! MODO SNAPSHOT INSEGURO !!! Use '--force-unsafe' para scripts.")
        if sys.stdin.isatty():
            if input("Digite 'CONCORDO': ").strip() != 'CONCORDO': sys.exit(1)
        else: sys.exit(1)

    try:
        conn = libvirt.open(CONNECT_URI)
        dom = conn.lookupByName(args.domain)
        CURRENT_DOMAIN_NAME = args.domain 

        try: 
            if dom.jobInfo()[0] != 0: subprocess.run(['virsh', 'domjobabort', args.domain], stderr=subprocess.DEVNULL)
        except: pass

        bkp_dir = os.path.join(args.backup_dir, args.domain)
        manage_retention(bkp_dir, args.retention_days)
        
        details = get_disk_details_from_xml(dom, args.disk)
        if not details: raise Exception("Discos não encontrados")
        
        clean, msg = check_clean_state(dom, details)
        if not clean:
            logger.error(f"\nABORTADO: {msg}\nAção: Execute 'virsh blockcommit {args.domain} <disk> --active --pivot'"); sys.exit(1)
            
        if not check_available_space(bkp_dir, details): sys.exit(1)
        
        if args.mode == 'libvirt': run_backup_libvirt_api(dom, bkp_dir, details, timestamp)
        else: run_backup_snapshot_cp(dom, bkp_dir, details, timestamp)
        
    except Exception as e: logger.exception(f"Fatal: {e}"); sys.exit(1)
    finally: 
        if 'conn' in locals() and conn: conn.close()