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
FILES_TO_CLEANUP = []      # Arquivos de DESTINO (.bak) parciais

# --- LOGGER ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG) 

def setup_logging(domain_name, timestamp):
    try:
        log_dir_final = LOG_DIR
        # Fallback para /tmp se não houver permissão em /var/log
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

    # 1. Abortar Job Libvirt (Se houver um ativo criado por este script)
    if BACKUP_JOB_RUNNING and CURRENT_DOMAIN_NAME:
        logger.warning("Tentando abortar o job Libvirt ativo...")
        try: 
            subprocess.run(['virsh', 'domjobabort', CURRENT_DOMAIN_NAME], check=True, capture_output=True)
            logger.info(" -> Job Libvirt abortado com sucesso.")
        except subprocess.CalledProcessError as e:
            logger.critical(f" -> FALHA ao abortar job. Pode ser necessário reiniciar a VM. Erro: {e.stderr}")
        BACKUP_JOB_RUNNING = False

    # 2. Limpar arquivos .bak parciais (Lixo gerado por falha)
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
        if dom.jobInfo()[0] != 0: return False, "Job Libvirt ativo detectado (BlockCommit ou Copy em andamento)."
        if dom.snapshotNum(0) > 0: return False, "Snapshot interno detectado (Não suportado com backup externo)."
    except: pass
    for dev, info in disk_details.items():
        if any(p in os.path.basename(info['path']) for p in FORBIDDEN_PATTERNS):
            return False, f"Disco '{dev}' parece ser um snapshot temporário ou sujo."
    return True, "Limpo"

def check_available_space(backup_dir, disk_details):
    # Calcula espaço necessário (Tamanho atual dos discos + margem de segurança)
    needed = sum([os.path.getsize(i['path']) for i in disk_details.values()]) * (1 + SAFETY_MARGIN_PERCENT)
    os.makedirs(backup_dir, exist_ok=True)
    
    free_space = shutil.disk_usage(backup_dir).free
    if needed > free_space:
        logger.error(f"Espaço insuficiente no destino. Necessário: {needed/1024**3:.2f}GB | Livre: {free_space/1024**3:.2f}GB")
        return False
    return True

# --- GERENCIAMENTO DE RETENÇÃO ---
def manage_retention(backup_dir, days):
    if not os.path.isdir(backup_dir): return
    cutoff = datetime.now() - timedelta(days=days)
    backups = []
    
    try:
        # Lista todos os backups .bak
        for f in os.listdir(backup_dir):
            if f.endswith('.bak'):
                fp = os.path.join(backup_dir, f)
                mtime = os.path.getmtime(fp)
                dt = datetime.fromtimestamp(mtime)
                
                # Extrai identidade do arquivo para agrupar (ex: vm-vda)
                match = re.search(r"(.+)-(\d{8}_\d{6})", f)
                if match:
                    identity = match.group(1) # ex: vm46176-vda
                    date_str = dt.strftime('%Y-%m-%d')
                    # Chave única: Data + Disco (permite diferenciar vda de vdb no mesmo dia)
                    unique_key = f"{date_str}_{identity}"
                else:
                    unique_key = f"{dt.strftime('%Y-%m-%d')}_{f}" # Fallback
                
                backups.append({'path': fp, 'dt': dt, 'unique_key': unique_key})
        
        # Ordena: Mais recente primeiro
        backups.sort(key=lambda x: x['dt'], reverse=True)
        
    except Exception as e: logger.error(f"Erro ao listar backups para retenção: {e}"); return

    if not backups:
        logger.info(f"--- RETENÇÃO: Nenhum arquivo encontrado. ---")
        return

    keep_list = []
    delete_list = []
    seen_keys = set()

    # Aplica as regras
    for b in backups:
        if b['unique_key'] in seen_keys:
            # Já existe um backup mais recente para este dia/disco
            delete_list.append((b, "Redundante do mesmo dia (Substituído pelo novo)"))
        else:
            seen_keys.add(b['unique_key'])
            if b['dt'] < cutoff:
                delete_list.append((b, "Expirado (Mais antigo que retenção)"))
            else:
                keep_list.append(b)

    # Trava de Segurança: Se for apagar TUDO, salva o último
    if not keep_list and delete_list:
        rescued, reason = delete_list.pop(0)
        keep_list.append(rescued)
        logger.warning(f"--- TRAVA DE SEGURANÇA: Mantendo último backup disponível ({os.path.basename(rescued['path'])}) mesmo estando expirado. ---")

    if sys.stdout.isatty(): print()
    logger.info(f"--- ANÁLISE DE RETENÇÃO ({days} dias) ---")
    
    if keep_list:
        logger.info("VÁLIDOS (Mantidos):")
        for b in keep_list: logger.info(f"   [OK] {os.path.basename(b['path'])} ({b['dt'].strftime('%d/%m %H:%M')})")
            
    if delete_list:
        logger.info("LIMPEZA (Serão removidos):")
        for b, reason in delete_list:
            logger.info(f"   [X] {os.path.basename(b['path'])}")
            logger.info(f"       Motivo: {reason}")
            try: 
                os.remove(b['path'])
                logger.info("       -> Removido com sucesso.")
            except Exception as e:
                logger.error(f"       -> Falha ao remover: {e}")
    
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

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp):
    global BACKUP_JOB_RUNNING, FILES_TO_CLEANUP
    
    logger.info("Iniciando rotina de backup (Sequencial)...")

    for dev, info in disk_details.items():
        fp = os.path.join(backup_dir, f"{dom.name()}-{dev}-{timestamp}.{DISK_FORMAT}.bak")
        FILES_TO_CLEANUP.append(fp) # Marca para limpeza em caso de crash
        
        # XML para UM disco (Full Backup)
        xml = f"<domainbackup><disks><disk name='{dev}' type='file'><target file='{fp}'/><driver type='{DISK_FORMAT}'/></disk></disks></domainbackup>"
        
        logger.info(f" -> Processando disco '{dev}'...")
        logger.info(f"    Origem:  {info['path']}")
        logger.info(f"    Destino: {fp}")
        
        total_bytes = os.path.getsize(info['path'])
        
        try:
            dom.backupBegin(xml, None, 0)
            BACKUP_JOB_RUNNING = True
            last_log_time = 0
            
            while True:
                stats = dom.jobStats()
                if not stats or stats.get('type', 0) == 0:
                    # Job terminou
                    if sys.stdout.isatty(): print(f"\r\033[KINFO: [{dev}] [OK] Backup concluído.")
                    BACKUP_JOB_RUNNING = False
                    break
                
                # Verifica erros reportados pelo Libvirt
                if stats.get('status') == libvirt.VIR_DOMAIN_JOB_FAILED:
                     raise Exception("Libvirt reportou falha no Job.")

                current_time = time.time()
                if sys.stdout.isatty():
                    monitor_progress(dev, fp, total_bytes)
                elif current_time - last_log_time > 60:
                    # Log menos verboso se não for terminal interativo
                    logger.info(f"Progresso [{dev}]: {os.path.getsize(fp)}/{total_bytes}")
                    last_log_time = current_time
                time.sleep(0.5)
                
        except Exception as e:
            logger.error(f"ERRO durante backup de '{dev}': {e}")
            perform_cleanup() # Limpa o arquivo atual defeituoso
            raise # Repassa o erro para abortar o script
        
        # Remove da lista de cleanup global pois já foi concluído com sucesso
        if fp in FILES_TO_CLEANUP: FILES_TO_CLEANUP.remove(fp)

    logger.info("Todos os discos foram copiados com sucesso!")

# --- MAIN ---
if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Backup KVM Live (Libvirt API) - CLI")
    
    # Argumentos Obrigatórios
    parser.add_argument('--domain', required=True, help="Nome da VM (Ex: vm46176)")
    parser.add_argument('--backup-dir', required=True, help="Diretório base de destino")
    parser.add_argument('--disk', required=True, nargs='+', help="Lista de discos para backup (Ex: vda vdb)")
    
    # Opcional (Padrão 7 dias)
    parser.add_argument('--retention-days', type=int, required=False, default=7, help="Dias de retenção (Padrão: 7)")
    
    args = parser.parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    setup_logging(args.domain, timestamp)
    
    conn = None
    try:
        conn = libvirt.open(CONNECT_URI)
        try:
            dom = conn.lookupByName(args.domain)
        except libvirt.libvirtError:
            logger.error(f"VM '{args.domain}' não encontrada no hypervisor.")
            sys.exit(1)
            
        CURRENT_DOMAIN_NAME = args.domain 

        # Tenta limpar jobs órfãos antes de começar
        try: 
            if dom.jobInfo()[0] != 0: 
                logger.warning("Job anterior detectado. Tentando abortar...")
                subprocess.run(['virsh', 'domjobabort', args.domain], stderr=subprocess.DEVNULL)
        except: pass

        bkp_dir = os.path.join(args.backup_dir, args.domain)
        
        # Obtém detalhes dos discos e valida estado
        details = get_disk_details_from_xml(dom, args.disk)
        if not details: raise Exception("Discos solicitados não encontrados.")
        
        clean, msg = check_clean_state(dom, details)
        if not clean:
            logger.error(f"\nABORTADO: {msg}\nA VM precisa estar limpa (sem snapshots ativos/jobs) para o backup.")
            sys.exit(1)
            
        if not check_available_space(bkp_dir, details): 
            sys.exit(1)
        
        # 1. EXECUTA O BACKUP
        run_backup_libvirt_api(dom, bkp_dir, details, timestamp)

        # 2. EXECUTA A RETENÇÃO (Apenas se chegou aqui sem erros)
        logger.info("Iniciando verificação de retenção e limpeza...")
        manage_retention(bkp_dir, args.retention_days)
        
        logger.info("PROCEDIMENTO FINALIZADO COM SUCESSO.")
        
    except Exception as e:
        logger.exception(f"ERRO FATAL: {e}")
        # A retenção NÃO roda aqui, protegendo os backups antigos
        sys.exit(1)
    finally: 
        if conn: conn.close()