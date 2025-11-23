#!/usr/bin/env python3
import libvirt
import sys
import os
import shutil
import time
import argparse
import subprocess
import signal
import re 
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
BACKUP_JOB_RUNNING = False
FILES_TO_CLEANUP = []      # Arquivos de DESTINO (.bak)

# --- LOGGER ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG) 

def setup_logging(domain_name, timestamp):
    try:
        log_dir_final = LOG_DIR
        # Se não tiver permissão no /var/log, usa /tmp
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

def perform_cleanup(exit_after=False):
    global BACKUP_JOB_RUNNING, CURRENT_DOMAIN_NAME
    
    if sys.stdout.isatty(): print() 
    logger.warning("--- PROTOCOLO DE LIMPEZA INICIADO ---")

    # 1. Abortar Job Libvirt (Única ação crítica restante)
    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        logger.warning("Tentando abortar o job Libvirt ativo...")
        try: 
            subprocess.run(['virsh', 'domjobabort', CURRENT_DOMAIN_NAME], check=True, capture_output=True)
            logger.info(" -> Job Libvirt abortado com sucesso.")
        except subprocess.CalledProcessError as e:
            logger.critical(f" -> FALHA ao abortar job. Pode ser necessário reiniciar a VM. Erro: {e.stderr}")
        BACKUP_JOB_RUNNING = False

    # 2. Limpar arquivos .bak parciais
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
    found_devs = []
    try:
        root = ET.fromstring(dom.XMLDesc(0))
        for device in root.findall('./devices/disk'):
            target = device.find('target')
            if target is not None:
                dev_name = target.get('dev')
                if dev_name in target_devs_list:
                    source = device.find('source')
                    if source is not None and source.get('file'):
                        details[dev_name] = {'path': source.get('file')}
                        found_devs.append(dev_name)
    except Exception as e: logger.error(f"Erro XML: {e}"); return None
        
    missing_devs = [d for d in target_devs_list if d not in found_devs]
    if missing_devs:
        logger.error("="*60)
        logger.error(f"ERRO FATAL: Discos solicitados não existem na VM: {missing_devs}")
        logger.error(f"Discos encontrados: {found_devs}")
        logger.error("Verifique o parâmetro --disk")
        logger.error("="*60)
        return None

    return details

def check_clean_state(dom, disk_details):
    try:
        if dom.jobInfo()[0] != 0: return False, "Job Libvirt ativo detectado."
        if dom.snapshotNum(0) > 0: return False, "Snapshot registrado detectado."
    except: pass
    for dev, info in disk_details.items():
        if any(p in os.path.basename(info['path']) for p in FORBIDDEN_PATTERNS):
            return False, f"Disco '{dev}' está sujo."
    return True, "Limpo"

def check_available_space(backup_dir, disk_details):
    needed = sum([os.path.getsize(i['path']) for i in disk_details.values()]) * (1 + SAFETY_MARGIN_PERCENT)
    os.makedirs(backup_dir, exist_ok=True)
    if needed > shutil.disk_usage(backup_dir).free:
        logger.error("Espaço insuficiente."); return False
    return True

# --- GERENCIAMENTO DE RETENÇÃO ---
def manage_retention(backup_dir, days):
    if not os.path.isdir(backup_dir): return
    cutoff = datetime.now() - timedelta(days=days)
    backups = []
    
    try:
        for f in os.listdir(backup_dir):
            if f.endswith('.bak'):
                fp = os.path.join(backup_dir, f)
                mtime = os.path.getmtime(fp)
                dt = datetime.fromtimestamp(mtime)
                
                match = re.search(r"(.+)-(\d{8}_\d{6})", f)
                if match:
                    identity = match.group(1)
                    date_str = dt.strftime('%Y-%m-%d')
                    unique_key = f"{date_str}_{identity}"
                else:
                    unique_key = f"{dt.strftime('%Y-%m-%d')}_{f}" # Fallback
                
                backups.append({'path': fp, 'dt': dt, 'unique_key': unique_key})
                
        backups.sort(key=lambda x: x['dt'], reverse=True)
        
    except Exception as e: logger.error(f"Erro listar backups: {e}"); return

    if not backups:
        logger.info(f"--- RETENÇÃO: Nenhum backup anterior encontrado. ---")
        return

    keep_list = []
    delete_list = []
    seen_keys = set()

    for b in backups:
        if b['unique_key'] in seen_keys:
            delete_list.append((b, "Redundante do mesmo dia"))
        else:
            seen_keys.add(b['unique_key'])
            if b['dt'] < cutoff: delete_list.append((b, "Expirado (Idade)"))
            else: keep_list.append(b)

    if not keep_list and delete_list:
        rescued, reason = delete_list.pop(0)
        keep_list.append(rescued)
        logger.warning(f"--- TRAVA DE SEGURANÇA: Mantendo último backup ({os.path.basename(rescued['path'])}) ---")

    if sys.stdout.isatty(): print()
    logger.info(f"--- ANÁLISE DE RETENÇÃO ({days} dias | 1 por dia/disco) ---")
    
    if keep_list:
        logger.info("VÁLIDOS (Mantidos):")
        for b in keep_list: logger.info(f"   -> {os.path.basename(b['path'])} ({b['dt'].strftime('%d/%m %H:%M')})")
            
    if delete_list:
        logger.info("EXPIRADOS (Analisando remoção...):")
        for b, reason in delete_list:
            logger.info(f"   -> {os.path.basename(b['path'])} - Motivo: {reason}")
            try: os.remove(b['path']); logger.info("      -> [OK] Removido.")
            except: pass
    
    logger.info("-" * 40)
    if sys.stdout.isatty(): print()

# --- MONITORAMENTO E BACKUP ---

def monitor_progress(dev_name, dest_file, total_bytes):
    spinner = "|/-\\"
    spin = spinner[int(time.time()*4)%4]
    curr = 0
    try:
        if os.path.exists(dest_file): curr = os.path.getsize(dest_file)
    except: pass
    perc = (curr / total_bytes * 100) if total_bytes > 0 else 0
    msg = f"INFO: [{dev_name}] [{spin}] {curr} / {total_bytes} Bytes ({perc:.1f}%)"
    if sys.stdout.isatty():
        sys.stdout.write(f"\r\033[K{msg}")
        sys.stdout.flush()
    return msg 

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp):
    global BACKUP_JOB_RUNNING, FILES_TO_CLEANUP
    
    # Executa SEQUENCIALMENTE (um Job por vez)
    for dev, info in disk_details.items():
        fp = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.{DISK_FORMAT}.bak")
        FILES_TO_CLEANUP.append(fp)
        
        # XML para UM disco
        xml = f"<domainbackup><disks><disk name='{dev}' type='file'><target file='{fp}'/><driver type='{DISK_FORMAT}'/></disk></disks></domainbackup>"
        
        logger.info(f" -> Destino ({dev}): {fp}")
        logger.info(f"[Libvirt] Iniciando Job para '{dev}'...")
        
        total_bytes = os.path.getsize(info['path'])
        
        try:
            dom.backupBegin(xml, None, 0)
            BACKUP_JOB_RUNNING = True
            last_log_time = 0
            
            while True:
                stats = dom.jobStats()
                if not stats or stats.get('type', 0) == 0:
                    if sys.stdout.isatty(): print(f"\r\033[KINFO: [{dev}] [OK] 100% Concluído.")
                    BACKUP_JOB_RUNNING = False
                    break
                
                current_time = time.time()
                if sys.stdout.isatty():
                    monitor_progress(dev, fp, total_bytes)
                elif current_time - last_log_time > 60:
                    logger.info(f"Progresso [{dev}]: {os.path.getsize(fp)}/{total_bytes}")
                    last_log_time = current_time
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"Erro Libvirt ({dev}): {e}"); perform_cleanup(); raise
        
        # Remove da lista de cleanup global pois já foi concluído com sucesso
        if fp in FILES_TO_CLEANUP: FILES_TO_CLEANUP.remove(fp)

    logger.info("Sucesso Total!")

# --- MAIN ---
if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Backup KVM Live (Libvirt API) - Modo Estrito CLI")
    
    # Argumentos agora são OBRIGATÓRIOS (required=True)
    parser.add_argument('--domain', required=True, help="Nome da VM (Ex: vm46176)")
    parser.add_argument('--backup-dir', required=True, help="Diretório base de destino")
    parser.add_argument('--disk', required=True, nargs='+', help="Lista de discos para backup (Ex: vda vdb)")
    
    # Argumento opcional com valor padrão em código
    parser.add_argument('--retention-days', type=int, required=False, default=7, help="Dias de retenção (Padrão: 7)")
    
    args = parser.parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    setup_logging(args.domain, timestamp)
    
    try:
        conn = libvirt.open(CONNECT_URI)
        try:
            dom = conn.lookupByName(args.domain)
        except libvirt.libvirtError:
            logger.error(f"VM '{args.domain}' não encontrada.")
            sys.exit(1)
            
        CURRENT_DOMAIN_NAME = args.domain 

        try: 
            # Aborta qualquer job anterior, se houver
            if dom.jobInfo()[0] != 0: subprocess.run(['virsh', 'domjobabort', args.domain], stderr=subprocess.DEVNULL)
        except: pass

        bkp_dir = os.path.join(args.backup_dir, args.domain)
        manage_retention(bkp_dir, args.retention_days)
        
        details = get_disk_details_from_xml(dom, args.disk)
        if not details: raise Exception("Discos solicitados não encontrados.")
        
        clean, msg = check_clean_state(dom, details)
        if not clean:
            logger.error(f"\nABORTADO: {msg}\nAção: Execute 'virsh blockcommit {args.domain} <disk> --active --pivot'"); sys.exit(1)
            
        if not check_available_space(bkp_dir, details): sys.exit(1)
        
        run_backup_libvirt_api(dom, bkp_dir, details, timestamp)
        
    except Exception as e: logger.exception(f"Fatal: {e}"); sys.exit(1)
    finally: 
        if 'conn' in locals() and conn: conn.close()
        