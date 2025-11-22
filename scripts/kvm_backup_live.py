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

# --- LIMPEZA INTELIGENTE (AUTO-CURA) ---

def remove_temp_source_file(dev_name):
    """Remove o arquivo _tmp_ do disco se ele existir."""
    global TEMP_SOURCE_FILES
    # Tenta remover baseando-se na lista global
    for f in list(TEMP_SOURCE_FILES):
        if f"_{dev_name}_" in os.path.basename(f):
            if os.path.exists(f):
                try: os.remove(f); logger.info(f" -> Arquivo temporário removido: {os.path.basename(f)}")
                except: pass
            if f in TEMP_SOURCE_FILES: TEMP_SOURCE_FILES.remove(f)

def get_dirty_disks_from_live_vm(domain_name):
    """
    [NOVO] Inspeciona a VM ao vivo para ver se ela está 'suja' (rodando em snapshot).
    Retorna uma lista de dispositivos que precisam de Pivot (ex: ['vda']).
    Isso substitui a confiança na variável global.
    """
    dirty_devs = []
    try:
        # Executa domblklist para ver a verdade nua e crua
        res = subprocess.run(['virsh', 'domblklist', domain_name], capture_output=True, text=True)
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    target = parts[0]
                    source = parts[1]
                    # Se o arquivo fonte tem padrão de temp, precisa de pivot
                    if any(p in os.path.basename(source) for p in FORBIDDEN_PATTERNS):
                        dirty_devs.append(target)
    except Exception as e:
        logger.error(f"Erro ao inspecionar estado da VM: {e}")
    return dirty_devs

def perform_cleanup(exit_after=False):
    global BACKUP_JOB_RUNNING, CURRENT_SNAPSHOT_NAME, CURRENT_DOMAIN_NAME
    
    if sys.stdout.isatty(): print() 
    logger.warning("--- PROTOCOLO DE LIMPEZA INICIADO ---")

    # 1. AUTO-CURA: Verifica estado real da VM
    if CURRENT_DOMAIN_NAME:
        dirty_disks = get_dirty_disks_from_live_vm(CURRENT_DOMAIN_NAME)
        
        if dirty_disks:
            logger.warning("!"*60)
            logger.warning(f"ALERTA: VM RODANDO EM DISCOS TEMPORÁRIOS: {dirty_disks}")
            logger.warning("INICIANDO PIVOT DE EMERGÊNCIA (BLOCKCOMMIT)...")
            logger.warning("!"*60)
            
            for dev in dirty_disks:
                try:
                    logger.info(f" -> Tentando recuperar '{dev}'...")
                    subprocess.run(
                        ['virsh', 'blockcommit', CURRENT_DOMAIN_NAME, dev, '--active', '--pivot'], 
                        check=True
                    )
                    logger.info(f" -> SUCESSO: '{dev}' recuperado.")
                    
                    # Se recuperou, tenta achar e apagar o arquivo tmp que sobrou
                    # (Tentativa de melhor esforço baseada no padrão de nome)
                    remove_temp_source_file(dev)
                    
                except subprocess.CalledProcessError:
                    logger.critical(f" -> FALHA CRÍTICA: '{dev}' não pôde ser recuperado automaticamente.")
                    logger.critical(f" -> Execute manualmente: virsh blockcommit {CURRENT_DOMAIN_NAME} {dev} --active --pivot")

    # 2. Abortar Job Libvirt (Se houver)
    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        try: subprocess.run(['virsh', 'domjobabort', CURRENT_DOMAIN_NAME], capture_output=True)
        except: pass
        BACKUP_JOB_RUNNING = False

    # 3. Remover Metadados Snapshot
    if CURRENT_SNAPSHOT_NAME and CURRENT_DOMAIN_NAME:
        try: subprocess.run(['virsh', 'snapshot-delete', CURRENT_DOMAIN_NAME, CURRENT_SNAPSHOT_NAME, '--metadata'], capture_output=True)
        except: pass
        CURRENT_SNAPSHOT_NAME = None

    # 4. Limpar arquivos .bak (Destino)
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
            logger.error(f"Erro CMD: {e.stderr}")
            raise
        else:
            raise

def check_agent_availability(dom):
    try:
        dom.qemuAgentCommand('{"execute":"guest-ping"}', 1, 0)
        return True
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
    except Exception as e:
        logger.error(f"Erro XML: {e}"); return None
    return details

def check_clean_state(dom, disk_details):
    try:
        if dom.snapshotNum(0) > 0: return False, "Snapshot registrado detectado."
    except: pass
    # Verifica estado atual dos discos
    for dev, info in disk_details.items():
        if any(p in os.path.basename(info['path']) for p in FORBIDDEN_PATTERNS):
            return False, f"Disco '{dev}' está sujo (usando arquivo temporário)."
    return True, "Limpo"

def check_available_space(backup_dir, disk_details):
    needed = sum([os.path.getsize(i['path']) for i in disk_details.values()]) * (1 + SAFETY_MARGIN_PERCENT)
    os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
    if needed > shutil.disk_usage(os.path.dirname(backup_dir)).free:
        logger.error("Espaço insuficiente."); return False
    return True

def manage_retention(backup_dir, days):
    if not os.path.isdir(backup_dir): return
    cutoff = datetime.now() - timedelta(days=days)
    for f in os.listdir(backup_dir):
        fp = os.path.join(backup_dir, f)
        if f.endswith('.bak') and os.path.isfile(fp):
            if datetime.fromtimestamp(os.path.getmtime(fp)) < cutoff:
                try: os.remove(fp); logger.info(f"Retenção: {f}")
                except: pass

# --- BACKUP MODES ---

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
        spinner = "|/-\\"
        
        while True:
            stats = dom.jobStats()
            if not stats or stats.get('type', 0) == 0:
                print(); logger.info("Sucesso!"); BACKUP_JOB_RUNNING = False; FILES_TO_CLEANUP = []; break
            
            curr = sum([os.path.getsize(f) for f in FILES_TO_CLEANUP if os.path.exists(f)])
            perc = (curr / total_bytes * 100) if total_bytes else 0
            
            msg = f"[{spinner[int(time.time()*4)%4]}] Tamanho: {curr} / {total_bytes} Bytes ({perc:.1f}%)"
            if sys.stdout.isatty(): sys.stdout.write(f"\rINFO: {msg}".ljust(80)); sys.stdout.flush()
            time.sleep(0.5)
    except Exception as e:
        logger.error(f"Erro Libvirt: {e}"); perform_cleanup(); raise

def run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit):
    global CURRENT_SNAPSHOT_NAME, FILES_TO_CLEANUP, TEMP_SOURCE_FILES
    snap_name = f"{dom.name()}_snap_{timestamp}"
    CURRENT_SNAPSHOT_NAME = snap_name
    
    cmd_base = ['virsh', 'snapshot-create-as', '--domain', dom.name(), '--name', snap_name, '--disk-only', '--atomic', '--no-metadata']
    cmds_rsync = []
    
    for dev, info in disk_details.items():
        tmp = os.path.join(os.path.dirname(info['path']), f"{dom.name()}_{dev}_tmp_{timestamp}.qcow2")
        TEMP_SOURCE_FILES.append(tmp) # Rastreia para limpeza

        dst = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.{DISK_FORMAT}.bak")
        FILES_TO_CLEANUP.append(dst)
        cmd_base.extend(['--diskspec', f"{dev},file={tmp},driver=qcow2"])
        
        rsync = ['rsync', '-ah', '--inplace', '--progress', info['path'], dst]
        if bwlimit > 0: rsync.insert(1, f"--bwlimit={bwlimit*1024}")
        cmds_rsync.append(rsync)

    try:
        logger.info("[Snapshot] Tentando criar snapshot...")
        use_quiesce = False
        
        if check_agent_availability(dom):
            try:
                run_subprocess(cmd_base + ['--quiesce'], allow_fail=True)
                use_quiesce = True
            except subprocess.CalledProcessError: pass
        
        if not use_quiesce:
            run_subprocess(cmd_base)
            logger.info(" -> Snapshot padrão criado.")

        logger.info("[Snapshot] Iniciando Rsync...")
        for cmd in cmds_rsync: subprocess.run(cmd, check=True)
            
        logger.info("[Snapshot] Fazendo Pivot...")
        for dev in disk_details.keys():
            subprocess.run(['virsh', 'blockcommit', dom.name(), dev, '--active', '--pivot'], check=True)
            # Remove arquivo _tmp_ do disco pois pivot funcionou
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
        CURRENT_DOMAIN_NAME = args.domain # Define global cedo para limpeza funcionar

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
        else: run_backup_snapshot_rsync(dom, bkp_dir, details, timestamp, args.bwlimit)
        
    except Exception as e: logger.exception(f"Fatal: {e}"); sys.exit(1)
    finally: 
        if 'conn' in locals() and conn: conn.close()