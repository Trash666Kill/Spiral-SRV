#!/usr/bin/env python3
import libvirt
import sys
import os
import shutil
import time
import argparse
import subprocess
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import importlib.metadata
import logging
import logging.handlers

# --- CONSTANTES ---
DISK_FORMAT = 'qcow2'
CONNECT_URI = 'qemu:///system'
SAFETY_MARGIN_PERCENT = 0.10
LOG_DIR = "/var/log/virsh"

# Constantes de índice para o modo legado (Fallback)
JOB_INFO_TYPE_INDEX = 0
JOB_INFO_PROCESSED_INDEX = 2
JOB_INFO_TOTAL_INDEX = 4

# --- Configuração do Logger ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG) 

def setup_logging(domain_name, timestamp):
    """Configura os handlers de logging para console e arquivo dinâmico."""
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        log_filename = f"{domain_name}-{timestamp}.log"
        log_path = os.path.join(LOG_DIR, log_filename)
        
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        logger.info(f"Log de arquivo para esta execução: {log_path}")

    except PermissionError:
        print(f"ERRO DE PERMISSÃO: Não é possível escrever em {LOG_DIR}.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERRO ao configurar o log de arquivo: {e}", file=sys.stderr)
        sys.exit(1)

# --- UTILS ---

def run_subprocess(command_list):
    """Helper para executar comandos de subprocesso e parar em caso de erro."""
    try:
        logger.info(f"Executando: {' '.join(command_list)}")
        result = subprocess.run(command_list, check=True, capture_output=True, text=True, encoding='utf-8')
        if result.stdout: logger.debug(f"STDOUT: {result.stdout.strip()}")
        if result.stderr: logger.debug(f"STDERR: {result.stderr.strip()}")
        return result
    except subprocess.CalledProcessError as e:
        logger.error(f"Comando falhou (Código: {e.returncode})")
        logger.error(f"  Comando: {' '.join(e.cmd)}")
        logger.error(f"  Stdout: {e.stdout}")
        logger.error(f"  Stderr: {e.stderr}")
        raise
    except Exception as e:
        logger.error(f"Falha inesperada ao executar subprocesso: {e}")
        raise

def get_disk_details_from_xml(dom, target_devs_list):
    """Analisa o XML da VM e extrai o caminho de origem para cada disco."""
    logger.info(f"Analisando XML para os discos: {', '.join(target_devs_list)}")
    details = {}
    try:
        raw_xml = dom.XMLDesc(0)
        root = ET.fromstring(raw_xml)
        target_set = set(target_devs_list)
        
        for device in root.findall('./devices/disk'):
            target = device.find('target')
            if target is None: continue
                
            target_name = target.get('dev')
            if target_name in target_set:
                source = device.find('source')
                if source is None or source.get('file') is None:
                    logger.warning(f"Disco {target_name} encontrado sem 'source file'. Ignorando.")
                    continue
                    
                driver = device.find('driver')
                details[target_name] = {
                    'path': source.get('file'),
                    'driver_type': driver.get('type') if driver is not None else 'desconhecido'
                }
                logger.info(f"  -> Encontrado '{target_name}': {details[target_name]['path']}")
                target_set.remove(target_name)
    
    except Exception as e:
        logger.error(f"Falha ao analisar o XML da VM: {e}")
        return None

    if len(target_set) > 0:
        logger.error(f"Não foi possível encontrar: {', '.join(target_set)}")
        return None
    return details

def manage_retention(backup_dir, retention_days):
    """Limpa backups antigos no diretório com base na idade (dias)."""
    logger.info("Verificando política de retenção...")
    logger.info(f"  -> Regra: Reter backups por no máximo {retention_days} dias.")

    if not os.path.isdir(backup_dir):
        logger.warning("Diretório de backup ainda não existe. Pulando retenção.")
        return

    now = datetime.now()
    cutoff_date = now - timedelta(days=retention_days)
    
    try:
        files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) 
                 if os.path.isfile(os.path.join(backup_dir, f)) and f.endswith('.bak')]
        
        if not files:
            logger.info("Nenhum backup antigo (.bak) encontrado.")
            return

        for_removal = set()
        kept_backups = []

        for f in files:
            try:
                mtime = os.path.getmtime(f)
                if datetime.fromtimestamp(mtime) < cutoff_date:
                    logger.warning(f"Retenção: Expirado -> {os.path.basename(f)}")
                    for_removal.add(f)
                else:
                    kept_backups.append(f)
            except OSError:
                continue

        if for_removal:
            logger.warning("Removendo backups antigos...")
            for f in for_removal:
                try:
                    os.remove(f)
                    logger.info(f"    -> Removido: {os.path.basename(f)}")
                except OSError as e:
                    logger.error(f"Falha ao remover {f}: {e}")
        else:
            logger.info("Nenhum backup para remover.")

    except Exception as e:
        logger.error(f"Falha ao processar retenção: {e}")

def check_available_space(backup_dir, disk_details):
    """Verifica espaço em disco com margem de segurança."""
    logger.info("Verificando espaço em disco...")
    try:
        total_size_needed = 0
        for dev, info in disk_details.items():
            disk_size = os.path.getsize(info['path'])
            total_size_needed += disk_size

        final_size_needed = total_size_needed * (1 + SAFETY_MARGIN_PERCENT)
        os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
        usage = shutil.disk_usage(os.path.dirname(backup_dir)) # Verifica no pai se o filho nao existir
        
        logger.info(f"  -> Necessário (com margem): {final_size_needed / (1024**3):.2f} GB")
        logger.info(f"  -> Disponível (destino):    {usage.free / (1024**3):.2f} GB")

        if final_size_needed > usage.free:
            raise Exception("Espaço insuficiente no dispositivo de backup.")
        return True
    except Exception as e:
        logger.error(f"Na verificação de espaço: {e}")
        return False

# [NOVO] Helper para verificar QEMU Guest Agent
def check_agent_availability(dom):
    """
    Verifica se o QEMU Guest Agent está respondendo enviando um ping.
    Retorna True se disponível, False caso contrário.
    """
    try:
        logger.info("Verificando disponibilidade do QEMU Guest Agent...")
        # Timeout de 1 segundo apenas para testar
        dom.qemuAgentCommand('{"execute":"guest-ping"}', 1, 0)
        logger.info("  -> Agente detectado e respondendo.")
        return True
    except libvirt.libvirtError:
        logger.warning("  -> Agente NÃO detectado ou não respondendo.")
        return False

# --- LÓGICA DE BACKUP (MODO LIBVIRT API) ---

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp):
    """Executa o backup usando a API nativa dom.backupBegin()."""
    
    # [MODIFICADO] A retenção foi removida daqui e movida para run_backup
    
    logger.info("[Modo Libvirt] Gerando XML de backup...")
    
    domain_name = dom.name()
    backup_files_map = {} 
    backup_started = False
    
    try:
        backup_xml_parts = ["<domainbackup><disks>"]
        
        for target_dev, info in disk_details.items():
            backup_filename = f"{domain_name}-{target_dev}-{timestamp}.{DISK_FORMAT}.bak"
            backup_file_path = os.path.join(backup_dir, backup_filename)
            backup_files_map[target_dev] = backup_file_path
            
            xml_disk_entry = f"""
<disk name='{target_dev}' type='file'>
  <target file='{backup_file_path}'/>
  <driver type='{DISK_FORMAT}'/>
</disk>
"""
            backup_xml_parts.append(xml_disk_entry)
            logger.info(f"  -> Incluindo disco '{target_dev}' para -> {backup_file_path}")

        backup_xml_parts.append("</disks></domainbackup>")
        backup_xml = "".join(backup_xml_parts)
        
        logger.info("[Modo Libvirt] Iniciando Backup Live...")
        start_time = time.time()

        dom.backupBegin(backup_xml, None, 0)
        backup_started = True 
        
        last_log_time = time.time()
        
        while True:
            job_info = dom.jobInfo()
            
            try:
                job_type = job_info.type
                data_total = job_info.dataTotal
            except AttributeError:
                job_type = job_info[JOB_INFO_TYPE_INDEX]
                data_total = job_info[JOB_INFO_TOTAL_INDEX]
            
            total_mb = data_total / 1024**2
            now = time.time()
            elapsed = now - start_time
            
            if now - last_log_time > 10:
                logger.info(f"Progresso: (Aguardando {total_mb:.0f} MB... {elapsed:.1f}s)")
                last_log_time = now

            if job_type == libvirt.VIR_DOMAIN_JOB_NONE:
                logger.info("==================================================")
                logger.info("Backup concluído com sucesso! (Modo Libvirt)")
                logger.info(f"Tempo total: {(elapsed / 60):.2f} minutos")
                break
            
            time.sleep(1)
            
    except libvirt.libvirtError as e:
        logger.error(f"Erro na Libvirt: {e}")
        if backup_started:
            try:
                subprocess.run(['virsh', 'domjobabort', domain_name], check=True, capture_output=True)
            except Exception:
                pass
        raise

# --- LÓGICA DE BACKUP (MODO SNAPSHOT + RSYNC) ---

def run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit_mb):
    """Executa o backup usando a abordagem Snapshot + Rsync + Blockcommit."""
    logger.info("[Modo Snapshot] Iniciando backup via Snapshot + Rsync...")
    
    domain_name = dom.name()
    logical_snapshot_name = f"{domain_name}_snapshot_{timestamp}"
    
    snapshot_files_map = {}
    snapshot_created = False
    
    try:
        # Comandos Base
        cmd_snapshot_base = [
            'virsh', 'snapshot-create-as', 
            '--domain', domain_name, 
            '--name', logical_snapshot_name,
            '--disk-only', '--atomic'
        ]
        
        cmds_rsync = []
        cmds_commit = []
        
        for target_dev, info in disk_details.items():
            base_path = info['path']
            base_dir = os.path.dirname(base_path)
            snap_filename = f"{domain_name}_{target_dev}_snapshot_{timestamp}.qcow2"
            snap_path = os.path.join(base_dir, snap_filename)
            bak_filename = f"{domain_name}-{target_dev}-{timestamp}.{DISK_FORMAT}.bak"
            bak_path = os.path.join(backup_dir, bak_filename)
            
            snapshot_files_map[target_dev] = {'base': base_path, 'snap': snap_path, 'bak': bak_path}
            
            diskspec = f"{target_dev},file={snap_path},driver=qcow2"
            cmd_snapshot_base.extend(['--diskspec', diskspec])
            
            rsync_cmd = ['rsync', '-avh', '--inplace']
            if bwlimit_mb > 0: rsync_cmd.append(f"--bwlimit={bwlimit_mb*1024}")
            rsync_cmd.extend([base_path, bak_path])
            
            cmds_rsync.append({'dev': target_dev, 'cmd': rsync_cmd})
            cmds_commit.append({'dev': target_dev, 'cmd': ['virsh', 'blockcommit', domain_name, target_dev, '--active', '--pivot', '--verbose']})

        # --- [MODIFICADO] PASSO 1: Lógica Otimizada de Snapshot com Check de Agente ---
        logger.info(f"[Modo Snapshot] PASSO 1: Criando snapshot '{logical_snapshot_name}'...")
        start_time = time.time()
        
        # Verifica se o agente existe antes de tentar quiesce
        agent_available = check_agent_availability(dom)
        quiesce_success = False

        if agent_available:
            cmd_snapshot_quiesce = cmd_snapshot_base + ['--quiesce']
            max_attempts = 3
            
            for attempt in range(max_attempts):
                try:
                    logger.info(f"  -> Tentativa {attempt + 1} de {max_attempts} com '--quiesce'...")
                    run_subprocess(cmd_snapshot_quiesce)
                    quiesce_success = True
                    snapshot_created = True 
                    logger.info("  -> Snapshot com '--quiesce' criado com sucesso.")
                    break 
                except Exception:
                    logger.warning(f"Falha na tentativa {attempt + 1} com '--quiesce'.")
                    if attempt < max_attempts - 1: time.sleep(15)
        else:
            logger.info("Pulando tentativas de '--quiesce' pois o agente não está disponível.")

        # Fallback: Se o agente não existe OU se todas as tentativas com quiesce falharam
        if not quiesce_success:
            if agent_available: logger.warning("Falha ao usar '--quiesce'. Tentando snapshot padrão (sem congelamento).")
            else: logger.info("Executando snapshot padrão (sem congelamento).")
            
            # O cmd_snapshot_base já não tem a flag --quiesce
            try:
                run_subprocess(cmd_snapshot_base)
                snapshot_created = True
                logger.info("  -> Snapshot padrão criado com sucesso.")
            except Exception as e:
                logger.error("Falha catastrófica: A criação do snapshot falhou.")
                raise e 
        
        logger.info("Discos base estão congelados/liberados para leitura.")

        # PASSO 2: Rsync
        logger.info("[Modo Snapshot] PASSO 2: Executando rsync...")
        for item in cmds_rsync:
            run_subprocess(item['cmd'])
        
        # PASSO 3: Pivot
        logger.info("[Modo Snapshot] PASSO 3: Executando blockcommit (pivot)...")
        for item in cmds_commit:
            run_subprocess(item['cmd'])
            
        # PASSO 4: Limpeza
        logger.info("[Modo Snapshot] PASSO 4: Removendo metadados...")
        run_subprocess(['virsh', 'snapshot-delete', domain_name, logical_snapshot_name, '--metadata'])
        
        logger.info("==================================================")
        logger.info("Backup concluído com sucesso! (Modo Snapshot)")
        logger.info(f"Tempo total: {(time.time() - start_time)/60:.2f} minutos")

    except Exception:
        logger.error("Falha catastrófica durante o backup (Modo Snapshot).")
        if snapshot_created:
            logger.critical("PERIGO: VM PODE ESTAR EM SNAPSHOT. VERIFIQUE 'virsh domblklist'.")
        raise

# --- FUNÇÃO PRINCIPAL ---

def run_backup(domain_name, backup_base_dir, disk_targets, retention_days, backup_mode, bwlimit_mb, timestamp):
    
    conn = None
    dom = None
    
    try:
        conn = libvirt.open(CONNECT_URI)
        if not conn: raise Exception(f"Falha conexão {CONNECT_URI}")

        try:
            dom = conn.lookupByName(domain_name)
        except libvirt.libvirtError:
            logger.error(f"Domínio '{domain_name}' não encontrado.")
            sys.exit(1)

        # Verificação de Job Preso
        try:
            job_info = dom.jobInfo()
            job_type = job_info.type if hasattr(job_info, 'type') else job_info[JOB_INFO_TYPE_INDEX]

            if job_type != libvirt.VIR_DOMAIN_JOB_NONE:
                logger.warning(f"Job ativo (tipo {job_type}). Tentando abortar...")
                subprocess.run(['virsh', 'domjobabort', domain_name], check=True, capture_output=True)
                time.sleep(3)
        except Exception as e:
            logger.error(f"Erro ao limpar jobs anteriores: {e}")
            sys.exit(1)

        backup_dir = os.path.join(backup_base_dir, domain_name)
        
        # [MODIFICADO] Chamada de retenção movida para cá (Executa em AMBOS os modos)
        manage_retention(backup_dir, retention_days)
        
        os.makedirs(backup_dir, exist_ok=True)

        disk_details = get_disk_details_from_xml(dom, disk_targets)
        if disk_details is None: raise Exception("Erro nos detalhes dos discos.")

        # Verificação Snapshot-in-Snapshot
        for target_dev, info in disk_details.items():
            if "_snapshot_" in os.path.basename(info['path']):
                logger.critical(f"ABORTANDO: Disco '{target_dev}' já está em snapshot.")
                sys.exit(1)
            
        if not check_available_space(backup_dir, disk_details):
            sys.exit(1)

        # Roteador
        if backup_mode == 'libvirt':
            run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp)
        elif backup_mode == 'snapshot':
            run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit_mb)
        else:
            raise Exception(f"Modo desconhecido: {backup_mode}")

    except KeyboardInterrupt:
        logger.warning("\nInterrompido pelo usuário.")
        if backup_mode == 'libvirt' and dom:
             subprocess.run(['virsh', 'domjobabort', domain_name], capture_output=True)
        sys.exit(130) 

    except Exception as e:
        logger.exception(f"Erro fatal: {e}")
        sys.exit(1)
        
    finally:
        if conn: conn.close()

# --- EXECUÇÃO ---

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup Live KVM/QEMU")
    parser.add_argument('--domain', required=True, help="Nome da VM")
    parser.add_argument('--backup-dir', required=True, help="Diretório de destino")
    parser.add_argument('--disk', required=True, nargs='+', help="Discos (ex: vda)")
    parser.add_argument('--retention-days', type=int, default=7, help="Dias de retenção")
    parser.add_argument('--mode', choices=['libvirt', 'snapshot'], default='libvirt', help="Modo de backup")
    parser.add_argument('--bwlimit', type=int, default=0, help="Limite banda Rsync MB/s")
    
    args = parser.parse_args()
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    setup_logging(args.domain, timestamp)
    
    if args.mode == 'snapshot':
        logger.info("AVISO: Modo Snapshot selecionado. Requer confirmação manual.")
        # ... Código de confirmação visual omitido para brevidade, mas mantido a lógica ...
        print("\n*** MODO SNAPSHOT SELECIONADO (ARRISCADO) ***")
        confirm = input("Digite 'y' para confirmar: ").strip().lower()
        if confirm != 'y': sys.exit(1)
    
    run_backup(args.domain, args.backup_dir, args.disk, args.retention_days, args.mode, args.bwlimit, timestamp)