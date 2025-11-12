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
# O nome do arquivo de log agora é dinâmico

# Constantes de índice para o modo legado (Fallback)
JOB_INFO_TYPE_INDEX = 0
JOB_INFO_PROCESSED_INDEX = 2
JOB_INFO_TOTAL_INDEX = 4

# --- Configuração do Logger ---
logger = logging.getLogger('virsh_hotbkp')
logger.setLevel(logging.DEBUG) # Nível mais baixo para capturar tudo

def setup_logging(domain_name, timestamp):
    """Configura os handlers de logging para console e arquivo dinâmico."""
    try:
        # Tenta criar o diretório de log
        os.makedirs(LOG_DIR, exist_ok=True)

        # [MUDANÇA] Cria um nome de arquivo de log único para esta execução
        log_filename = f"{domain_name}-{timestamp}.log"
        log_path = os.path.join(LOG_DIR, log_filename)
        
        # Handler para o ARQUIVO (simples, não rotativo)
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG) # Grava tudo no arquivo
        file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # Handler para o CONSOLE
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO) # Mostra apenas INFO e acima no console
        console_formatter = logging.Formatter('%(levelname)s: %(message)s')
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        logger.info(f"Log de arquivo para esta execução: {log_path}")

    except PermissionError:
        print(f"ERRO DE PERMISSÃO: Não é possível escrever em {LOG_DIR}.", file=sys.stderr)
        print("Execute o script como root ou ajuste as permissões.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"ERRO ao configurar o log de arquivo: {e}", file=sys.stderr)
        sys.exit(1)

# --- UTILS ---

def run_subprocess(command_list):
    """
    Helper para executar comandos de subprocesso e parar em caso de erro.
    """
    try:
        logger.info(f"Executando: {' '.join(command_list)}")
        
        result = subprocess.run(command_list, 
                                check=True, 
                                capture_output=True, 
                                text=True,
                                encoding='utf-8')
        
        # Loga STDOUT/STDERR como DEBUG (só aparece no arquivo, não no console)
        if result.stdout:
            logger.debug(f"STDOUT: {result.stdout.strip()}")
        if result.stderr:
            logger.debug(f"STDERR: {result.stderr.strip()}")
            
        return result
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Comando falhou (Código: {e.returncode})")
        logger.error(f"  Comando: {' '.join(e.cmd)}")
        logger.error(f"  Stdout: {e.stdout}")
        logger.error(f"  Stderr: {e.stderr}")
        raise # Re-levanta a exceção para parar o script
    except Exception as e:
        logger.error(f"Falha inesperada ao executar subprocesso: {e}")
        raise

def get_disk_details_from_xml(dom, target_devs_list):
    """
    Analisa o XML da VM e extrai o caminho de origem (source file)
    para cada disco de destino (target dev) solicitado.
    """
    logger.info(f"Analisando XML para os discos: {', '.join(target_devs_list)}")
    details = {}
    try:
        raw_xml = dom.XMLDesc(0)
        root = ET.fromstring(raw_xml)
        
        target_set = set(target_devs_list)
        
        for device in root.findall('./devices/disk'):
            target = device.find('target')
            
            if target is None:
                continue
                
            target_name = target.get('dev')
            if target_name in target_set:
                source = device.find('source')
                if source is None or source.get('file') is None:
                    logger.warning(f"Disco {target_name} encontrado, mas não possui 'source file'. Ignorando.")
                    continue
                    
                driver = device.find('driver')
                details[target_name] = {
                    'path': source.get('file'),
                    'driver_type': driver.get('type') if driver is not None else 'desconhecido'
                }
                logger.info(f"  -> Encontrado '{target_name}': {details[target_name]['path']}")
                target_set.remove(target_name) # Otimização
    
    except Exception as e:
        logger.error(f"Falha ao analisar o XML da VM: {e}")
        return None

    if len(target_set) > 0:
        logger.error(f"Não foi possível encontrar os seguintes discos no XML da VM: {', '.join(target_set)}")
        return None

    return details

def manage_retention(backup_dir, retention_days):
    """
    Limpa backups antigos no diretório com base na idade (dias).
    """
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

        backups = []
        for f in files:
            try:
                backups.append((os.path.getmtime(f), f))
            except OSError:
                continue 
                
        backups.sort()

        for_removal = set()
        kept_backups = []

        # Loop simplificado: verifica *todos* os backups *apenas* pela idade
        for mtime, f in backups:
            if datetime.fromtimestamp(mtime) < cutoff_date:
                logger.warning(f"Retenção (Idade): Marcado para remoção (expirado): {os.path.basename(f)}")
                for_removal.add(f)
            else:
                kept_backups.append(f)

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

        logger.info("Backups mantidos (existentes):")
        if not kept_backups:
            logger.info("    -> Nenhum backup existente foi mantido.")
        else:
            for f in kept_backups:
                logger.info(f"    -> {os.path.basename(f)}")

    except Exception as e:
        logger.error(f"Falha ao processar retenção: {e}")


def check_available_space(backup_dir, disk_details):
    """
    Verifica se há espaço suficiente no destino para o backup,
    incluindo uma margem de segurança.
    """
    logger.info("Verificando espaço em disco...")
    
    try:
        total_size_needed = 0
        for dev, info in disk_details.items():
            try:
                disk_size = os.path.getsize(info['path'])
                total_size_needed += disk_size
                logger.info(f"  -> Disco '{dev}' ({info['path']}) requer {disk_size / (1024**3):.2f} GB")
            except OSError as e:
                raise Exception(f"Falha ao obter tamanho do disco {dev} em {info['path']}: {e}")

        final_size_needed = total_size_needed * (1 + SAFETY_MARGIN_PERCENT)
        
        os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
        
        usage = shutil.disk_usage(backup_dir)
        available_space = usage.free

        logger.info(f"  -> Tamanho total (origem): {total_size_needed / (1024**3):.2f} GB")
        logger.info(f"  -> Necessário (com margem): {final_size_needed / (1024**3):.2f} GB")
        logger.info(f"  -> Disponível (destino):    {available_space / (1024**3):.2f} GB")

        if final_size_needed > available_space:
            raise Exception("Espaço insuficiente no dispositivo de backup.")
            
        logger.info("  -> Espaço suficiente verificado.")
        return True

    except Exception as e:
        logger.error(f"Na verificação de espaço: {e}")
        return False

# --- LÓGICA DE BACKUP (MODO LIBVIRT API) ---

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp, retention_days):
    """
    Executa o backup usando a API nativa dom.backupBegin()
    """
    
    # Executa a retenção APENAS neste modo
    manage_retention(backup_dir, retention_days)
    
    logger.info("[Modo Libvirt] Gerando XML de backup...")
    
    domain_name = dom.name()
    backup_files_map = {} 
    backup_started = False
    
    try:
        backup_xml_parts = []
        backup_xml_parts.append("<domainbackup><disks>")
        
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
        
        job_mode_reported = False
        last_log_time = time.time() # Para o log de progresso
        
        while True:
            job_info = dom.jobInfo()
            elapsed_time = time.time() - start_time
            
            try:
                job_type = job_info.type
                data_total = job_info.dataTotal
                if not job_mode_reported:
                    logger.info("Modo de job detectado: Moderno (Objeto)")
                    job_mode_reported = True
            except AttributeError:
                if not job_mode_reported:
                    logger.warning("Modo de job detectado: Legado (Lista/Tupla)")
                    job_mode_reported = True
                job_type = job_info[JOB_INFO_TYPE_INDEX]
                data_total = job_info[JOB_INFO_TOTAL_INDEX]
            
            total_mb = data_total / 1024**2
            
            # Loga o progresso a cada 10s
            now = time.time()
            if now - last_log_time > 10:
                logger.info(f"Progresso: (Aguardando {total_mb:.0f} MB... {elapsed_time:.1f}s)")
                last_log_time = now

            if job_type == libvirt.VIR_DOMAIN_JOB_NONE:
                end_time = time.time()
                time_elapsed_min = (end_time - start_time) / 60
                
                logger.info("==================================================")
                logger.info("Backup concluído com sucesso! (Modo Libvirt)")
                logger.info(f"Tempo total: {time_elapsed_min:.2f} minutos")
                logger.info("Arquivos Gerados:")
                for dev, path in backup_files_map.items():
                    logger.info(f"  -> Disco {dev}: {path}")
                logger.info("==================================================")
                break
            
            time.sleep(1) # Poll a cada 1 segundo
            
    except libvirt.libvirtError as e:
        logger.error(f"Erro na Libvirt: {e}")
        if "cannot acquire state change lock" not in str(e):
            if backup_started:
                logger.warning("Tentando abortar o job preso via CLI para a próxima execução...")
                try:
                    subprocess.run(['virsh', 'domjobabort', domain_name], 
                                   check=True, 
                                   capture_output=True, 
                                   text=True)
                    logger.info("Comando 'virsh domjobabort' enviado.")
                except Exception as e_abort:
                    logger.error(f"Falha ao tentar domjobabort: {e_abort.stderr or e_abort}")
        raise
    
    except Exception as e:
        logger.exception(f"Erro inesperado no [Modo Libvirt]: {e}")
        raise

# --- LÓGICA DE BACKUP (MODO SNAPSHOT + RSYNC) ---

def run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit_mb):
    """
    Executa o backup usando a abordagem Snapshot + Rsync + Blockcommit.
    A RETENÇÃO NÃO É APLICADA AQUI.
    """
    logger.info("[Modo Snapshot] Iniciando backup via Snapshot + Rsync...")
    
    domain_name = dom.name()
    # Este é o NOME LÓGICO do snapshot (o "grupo" da operação)
    logical_snapshot_name = f"{domain_name}_snapshot_{timestamp}"
    
    snapshot_files_map = {}
    snapshot_created = False
    
    try:
        # --- Preparar Comandos ---
        
        cmd_snapshot_quiesce = [
            'virsh', 'snapshot-create-as', 
            '--domain', domain_name, 
            '--name', logical_snapshot_name,
            '--disk-only', '--atomic', '--quiesce'
        ]
        
        cmds_rsync = []
        cmds_commit = []
        
        logger.info("[Modo Snapshot] Preparando especificações de disco...")

        for target_dev, info in disk_details.items():
            base_path = info['path']
            
            base_dir = os.path.dirname(base_path)
            snap_filename = f"{domain_name}_{target_dev}_snapshot_{timestamp}.qcow2"
            snap_path = os.path.join(base_dir, snap_filename)
            
            bak_filename = f"{domain_name}-{target_dev}-{timestamp}.{DISK_FORMAT}.bak"
            bak_path = os.path.join(backup_dir, bak_filename)
            
            snapshot_files_map[target_dev] = {'base': base_path, 'snap': snap_path, 'bak': bak_path}
            
            diskspec = f"{target_dev},file={snap_path},driver=qcow2"
            cmd_snapshot_quiesce.extend(['--diskspec', diskspec])
            
            rsync_cmd_base = ['rsync', '-avh', '--inplace']
            
            if bwlimit_mb > 0:
                bwlimit_kb = bwlimit_mb * 1024
                rsync_cmd_base.append(f"--bwlimit={bwlimit_kb}")
                logger.info(f"     Limite de banda do rsync definido para {bwlimit_mb} MB/s ({bwlimit_kb} KB/s)")

            rsync_cmd_base.extend([base_path, bak_path])
            
            cmds_rsync.append({
                'dev': target_dev, 
                'cmd': rsync_cmd_base
            })
            
            cmds_commit.append({
                'dev': target_dev,
                'cmd': ['virsh', 'blockcommit', domain_name, target_dev, '--active', '--pivot', '--verbose']
            })
            
            logger.info(f"  -> Disco {target_dev}:")
            logger.info(f"     Base (Origem): {base_path}")
            logger.info(f"     Snap (Temp):   {snap_path}")
            logger.info(f"     Backup (Dest): {bak_path}")

        # --- Executar Comandos ---
        start_time = time.time()

        # PASSO 1: Lógica de retry para --quiesce
        logger.info(f"[Modo Snapshot] PASSO 1: Criando snapshot '{logical_snapshot_name}'...")
        
        max_quiesce_attempts = 3
        retry_delay_seconds = 15
        
        quiesce_success = False
        for attempt in range(max_quiesce_attempts):
            try:
                logger.info(f"  -> Tentativa {attempt + 1} de {max_quiesce_attempts} com '--quiesce'...")
                run_subprocess(cmd_snapshot_quiesce)
                quiesce_success = True
                snapshot_created = True 
                logger.info("  -> Snapshot com '--quiesce' criado com sucesso.")
                break 
            except Exception as e:
                logger.warning(f"Falha na tentativa {attempt + 1} com '--quiesce'.")
                
                if attempt < max_quiesce_attempts - 1:
                    logger.info(f"Aguardando {retry_delay_seconds}s antes de tentar novamente...")
                    time.sleep(retry_delay_seconds)
                else:
                    logger.error(f"Todas as {max_quiesce_attempts} tentativas com '--quiesce' falharam.")

        # Fallback se as tentativas com --quiesce falharam
        if not quiesce_success:
            logger.warning("Não foi possível criar snapshot com '--quiesce'.")
            logger.warning("Tentando uma última vez SEM '--quiesce'. O backup pode não ser 100% consistente.")
            
            cmd_snapshot_no_quiesce = [item for item in cmd_snapshot_quiesce if item != '--quiesce']
            
            try:
                run_subprocess(cmd_snapshot_no_quiesce)
                snapshot_created = True
                logger.info("  -> Snapshot SEM '--quiesce' criado com sucesso.")
            except Exception as e:
                logger.error("Falha catastrófica: A tentativa final sem '--quiesce' também falhou.")
                raise e 
        
        logger.info("Discos base estão congelados.")

        # PASSO 2: Fazer backup (rsync)
        logger.info("[Modo Snapshot] PASSO 2: Executando rsync dos discos base...")
        for item in cmds_rsync:
            logger.info(f"Sincronizando disco {item['dev']}...")
            run_subprocess(item['cmd'])
            logger.info(f"Sincronização de {item['dev']} concluída.")
        
        # PASSO 3: Blockcommit (Pivot)
        logger.info("[Modo Snapshot] PASSO 3: Executando blockcommit (pivot) para mesclar dados...")
        for item in cmds_commit:
            logger.info(f"Mesclando disco {item['dev']}...")
            run_subprocess(item['cmd'])
            logger.info(f"Mesclagem de {item['dev']} concluída.")
            
        # PASSO 4: Limpar metadados do snapshot
        logger.info("[Modo Snapshot] PASSO 4: Removendo metadados do snapshot...")
        cmd_snap_delete = ['virsh', 'snapshot-delete', domain_name, logical_snapshot_name, '--metadata']
        run_subprocess(cmd_snap_delete)
        
        end_time = time.time()
        time_elapsed_min = (end_time - start_time) / 60

        logger.info("==================================================")
        logger.info("Backup concluído com sucesso! (Modo Snapshot)")
        logger.info(f"Tempo total: {time_elapsed_min:.2f} minutos")
        logger.info("INFO: Os arquivos de snapshot temporários (ex: *.qcow2.snap) são removidos automaticamente pelo libvirt após o blockcommit.")
        logger.info("Arquivos Gerados:")
        for dev, paths in snapshot_files_map.items():
            logger.info(f"  -> Disco {dev}: {paths['bak']}")
        logger.info("==================================================")

    except Exception as e:
        logger.error("Falha catastrófica durante o backup (Modo Snapshot).")
        
        if not snapshot_created:
            logger.warning("Falha ocorreu ANTES da criação do snapshot.")
            logger.info("O estado da VM deve estar normal. Nenhum snapshot foi criado.")
        else:
            logger.critical("******************************************************")
            logger.critical("* ATTENTION: FALHA CRÍTICA APÓS CRIAÇÃO DO SNAPSHOT *")
            logger.critical("******************************************************")
            logger.warning("A VM PODE ESTAR EM ESTADO INCONSISTENTE.")
            logger.warning(f"O snapshot '{logical_snapshot_name}' PODE AINDA ESTAR ATIVO.")
            logger.warning("Os discos podem estar apontando para:")
            for dev, paths in snapshot_files_map.items():
                 logger.warning(f"    -> {dev}: {paths['snap']}")
            logger.critical("AÇÃO MANUAL É PROVAVELMENTE NECESSÁRIA.")
            logger.warning(f"Verifique com 'virsh domblklist {domain_name}' e 'virsh snapshot-list {domain_name}'.")
            logger.warning("Pode ser necessário executar 'virsh blockcommit' e 'virsh snapshot-delete' manualmente.")

        logger.warning("Removendo arquivos de backup (.bak) parciais desta execução...")
        for dev, paths in snapshot_files_map.items():
            if os.path.exists(paths['bak']):
                try:
                    os.remove(paths['bak'])
                    logger.info(f"    -> Removido: {os.path.basename(paths['bak'])}")
                except OSError as e_rm:
                    logger.error(f"Falha ao remover {paths['bak']}: {e_rm}")
        
        raise # Propaga o erro para o handler principal


# --- FUNÇÃO PRINCIPAL ---

def run_backup(domain_name, backup_base_dir, disk_targets, retention_days, backup_mode, bwlimit_mb, timestamp):
    
    conn = None
    dom = None
    
    try:
        logger.info(f"Conectando ao hypervisor em: {CONNECT_URI}")
        conn = libvirt.open(CONNECT_URI)
        if conn is None:
            raise Exception(f"Falha ao abrir conexão com o hypervisor em {CONNECT_URI}")

        logger.info("--- Diagnóstico de Versão ---")
        try:
            py_ver = importlib.metadata.version('libvirt-python')
            logger.info(f"  -> Versão libvirt-python: {py_ver}")
        except importlib.metadata.PackageNotFoundError:
            logger.warning("  -> Versão libvirt-python: Não encontrada via metadata.")

        try:
            daemon_ver_int = conn.getVersion()
            major = daemon_ver_int // 1000000
            minor = (daemon_ver_int % 1000000) // 1000
            release = daemon_ver_int % 1000
            logger.info(f"  -> Versão libvirt-daemon (serviço): {major}.{minor}.{release}")
        except Exception as e:
            logger.warning(f"  -> Versão libvirt-daemon (serviço): Falha ao obter ({e})")
        logger.info("-------------------------------")

        try:
            dom = conn.lookupByName(domain_name)
            logger.info(f"Domínio '{domain_name}' encontrado.")
        except libvirt.libvirtError:
            logger.error(f"Domínio '{domain_name}' não encontrado.")
            sys.exit(1)

        # --- VERIFICAÇÃO DE JOB PRESO ---
        logger.info("Verificando se há jobs de backup presos...")
        try:
            job_info = dom.jobInfo()
            
            try:
                job_type = job_info.type
            except AttributeError:
                job_type = job_info[JOB_INFO_TYPE_INDEX]

            if job_type != libvirt.VIR_DOMAIN_JOB_NONE:
                logger.warning(f"Um job (tipo {job_type}) já está em execução para este domínio.")
                logger.warning("Tentando abortar o job anterior (via CLI) para iniciar o novo backup...")
                
                try:
                    subprocess.run(['virsh', 'domjobabort', domain_name], 
                                   check=True, 
                                   capture_output=True, 
                                   text=True)
                    logger.info("Comando 'virsh domjobabort' enviado.")
                    logger.info("Aguardando 3s para o job ser limpo...")
                    time.sleep(3) 
                except Exception as e_abort_cli:
                    error_output = e_abort_cli.stderr if hasattr(e_abort_cli, 'stderr') else str(e_abort_cli)
                    logger.error(f"Falha ao tentar 'virsh domjobabort': {error_output}")
                    logger.error("O backup não pode continuar.")
                    sys.exit(1)
                    
            else:
                logger.info("Nenhum job ativo encontrado. O backup pode prosseguir.")

        except libvirt.libvirtError as e_jobinfo:
            logger.error(f"Falha ao verificar informações do job: {e_jobinfo}")
            sys.exit(1)
        # --- [FIM] VERIFICAÇÃO DE JOB PRESO ---

        backup_dir = os.path.join(backup_base_dir, domain_name)
        os.makedirs(backup_dir, exist_ok=True)
        # [MUDANÇA] timestamp é recebido como argumento, não mais gerado aqui

        disk_details = get_disk_details_from_xml(dom, disk_targets)
        if disk_details is None:
            raise Exception("Falha ao obter detalhes dos discos. Verifique os logs acima.")

        # --- VERIFICAÇÃO DE PRÉ-EXECUÇÃO (Snapshot em Snapshot) ---
        logger.info("Verificando se os discos de origem já são snapshots...")
        disks_on_snapshot = []
        
        for target_dev, info in disk_details.items():
            disk_filename = os.path.basename(info['path'])
            
            if "_snapshot_" in disk_filename:
                disks_on_snapshot.append(target_dev)
                logger.critical(f"PERIGO: Disco '{target_dev}' já está rodando em um snapshot:")
                logger.critical(f"     {disk_filename}")

        if disks_on_snapshot:
            logger.critical("******************************************************")
            logger.critical("* ABORTANDO: BACKUP EM DISCO JÁ SNAPSHOTADO *")
            logger.critical("******************************************************")
            logger.warning("Os seguintes discos já estão em um estado de snapshot:")
            for dev in disks_on_snapshot:
                logger.warning(f"  - {dev}")
            logger.warning("Executar um novo backup (especialmente o modo snapshot) é perigoso.")
            logger.warning("Por favor, consolide (commit) os snapshots pendentes antes de continuar.")
            logger.warning(f"Use 'virsh domblklist {domain_name}' e 'virsh blockcommit' para corrigir.")
            sys.exit(1)
        else:
            logger.info("  -> Verificação OK. Nenhum disco de origem está em estado de snapshot.")
        # --- [FIM] VERIFICAÇÃO DE PRÉ-EXECUÇÃO ---
            
        if not check_available_space(backup_dir, disk_details):
            sys.exit(1)

        # --- ROTEADOR DO MODO DE BACKUP ---
        
        if backup_mode == 'libvirt':
            run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp, retention_days)
        
        elif backup_mode == 'snapshot':
            run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp, bwlimit_mb)
        
        else:
            raise Exception(f"Modo de backup desconheido: {backup_mode}")

    except KeyboardInterrupt:
        logger.warning("\nINTERRUPÇÃO: Script interrompido pelo usuário (Ctrl+C).")
        
        if backup_mode == 'libvirt' and dom is not None:
             logger.warning("Tentando abortar o job de backup (via CLI)...")
             try:
                 subprocess.run(['virsh', 'domjobabort', domain_name], 
                                check=True, 
                                capture_output=True, 
                                text=True)
                 logger.info("Comando 'virsh domjobabort' enviado.")
             except Exception as e_abort_cli:
                 error_output = e_abort_cli.stderr if hasattr(e_abort_cli, 'stderr') else str(e_abort_cli)
                 logger.error(f"Falha ao tentar domjobabort: {error_output}")
        
        logger.error("Script encerrado.")
        sys.exit(130) 

    except Exception as e:
        logger.exception(f"Erro inesperado na execução principal: {e}")
        sys.exit(1)
        
    finally:
        if conn:
            conn.close()
            logger.info("Conexão com o hypervisor fechada.")

# --- EXECUÇÃO ---

if __name__ == "__main__":
    
    parser = argparse.ArgumentParser(description="Script de backup live KVM/QEMU com seleção de disco.")
    
    parser.add_argument('--domain', 
                        required=True, 
                        help="Nome do domínio (VM) a ser feito o backup, e.g., 'win10'.")
    
    parser.add_argument('--backup-dir', 
                        required=True, 
                        help="Diretório base onde os backups serão armazenados, e.g., '/mnt/backups/'.")
                        
    parser.add_argument('--disk', 
                        required=True, 
                        nargs='+',
                        help="Um ou mais alvos de disco para o backup (ex: vda vdb vdc).")
    
    parser.add_argument('--retention-days',
                        type=int,
                        default=7,
                        help="Reter backups por no máximo X dias (Padrão: 7). Aplicável apenas ao modo 'libvirt'.")
                        
    parser.add_argument('--mode',
                        choices=['libvirt', 'snapshot'],
                        default='libvirt',
                        help="Modo de backup: 'libvirt' (API nativa dom.backupBegin) ou 'snapshot' (Snapshot + Rsync + Blockcommit). Padrão: libvirt.")
    
    parser.add_argument('--bwlimit',
                        type=int,
                        default=0,
                        help="Limita a largura de banda do rsync (em MB/s). (Padrão: 0 = ilimitado). Aplicável apenas ao modo 'snapshot'.")

    
    args = parser.parse_args()
    
    # [MUDANÇA] Gerar timestamp e configurar logging PRIMEIRO
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    setup_logging(args.domain, timestamp)
    
    # --- Confirmação de Modo Snapshot ---
    # (Usa print/input direto para cores e interação, mas loga o resultado)
    if args.mode == 'snapshot':
        logger.info("Modo snapshot detectado. Exibindo aviso de segurança interativo.")
        # Códigos de cor SÓ para este bloco
        RED_TERM = '\033[31m'
        YELLOW_TERM = '\033[33m'
        RESET_TERM = '\033[0m'
        CYAN_TERM = '\033[36m'
        
        print(f"\n{RED_TERM}******************************************************")
        print(f"* {YELLOW_TERM}AVISO DE MODO PERIGOSO: MODO SNAPSHOT SELECIONADO{RED_TERM} *")
        print(f"******************************************************{RESET_TERM}")
        print(f"{YELLOW_TERM}Este modo (snapshot + rsync + blockcommit) é {RED_TERM}ALTAMENTE ARRISCADO{YELLOW_TERM}.")
        print("Se o script falhar no meio do processo (ex: falta de espaço, erro no rsync),")
        print(f"a VM pode ficar em um {RED_TERM}ESTADO INCONSISTENTE{YELLOW_TERM} ou {RED_TERM}CORROMPIDO{YELLOW_TERM}.")
        print(f"{YELLOW_TERM}Benefícios: Pode ser mais rápido (especialmente com rsync delta).")
        print(f"Riscos:     Requer intervenção manual ('virsh blockcommit') em caso de falha.")
        print(f" -> {RED_TERM}Use por sua conta e risco.{RESET_TERM}")
        
        try:
            print("\nPara continuar, digite 'y' e pressione Enter:")
            confirm1 = input(f"{CYAN_TERM}> {RESET_TERM}").strip().lower()
            
            if confirm1 != 'y':
                logger.error("ABORTADO: Primeira confirmação falhou.")
                sys.exit(1)
            
            print("\nTem certeza? Esta ação é arriscada. Digite 'y' novamente para confirmar:")
            confirm2 = input(f"{CYAN_TERM}> {RESET_TERM}").strip().lower()
            
            if confirm2 != 'y':
                logger.error("ABORTADO: Segunda confirmação falhou.")
                sys.exit(1)
            
            logger.info("Confirmação dupla recebida. Iniciando o backup em modo snapshot...")
            time.sleep(2) # Pausa para o usuário ler
            
        except KeyboardInterrupt:
            logger.error("\nABORTADO: Operação cancelada pelo usuário.")
            sys.exit(130)
    # --- [FIM] Confirmação de Modo Snapshot ---
    
    try:
        # [MUDANÇA] Passa o timestamp para a função principal
        run_backup(args.domain, args.backup_dir, args.disk, args.retention_days, args.mode, args.bwlimit, timestamp)
    except Exception as e:
        logger.critical(f"Uma exceção não tratada encerrou o script: {e}")
        sys.exit(1)