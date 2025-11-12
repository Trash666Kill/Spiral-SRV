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

# --- ANSI Color Codes ---
GREEN = '\033[32m'
RED = '\033[31m'
YELLOW = '\033[33m'
RESET = '\033[0m'
CYAN = '\033[36m'

# --- CONSTANTES ---
DISK_FORMAT = 'qcow2'
CONNECT_URI = 'qemu:///system'
SAFETY_MARGIN_PERCENT = 0.10

# Constantes de índice para o modo legado (Fallback)
JOB_INFO_TYPE_INDEX = 0
JOB_INFO_PROCESSED_INDEX = 2
JOB_INFO_TOTAL_INDEX = 4


# --- UTILS ---

def run_subprocess(command_list):
    """
    Helper para executar comandos de subprocesso e parar em caso de erro.
    """
    try:
        # Imprime o comando para fins de depuração (esconde informações sensíveis se houver)
        print(f"  -> {CYAN}Executando:{RESET} {' '.join(command_list)}")
        
        # NOTA: text=True é o mesmo que universal_newlines=True
        result = subprocess.run(command_list, 
                                check=True, 
                                capture_output=True, 
                                text=True,
                                encoding='utf-8')
        
        if result.stdout:
            print(f"     {GREEN}STDOUT:{RESET} {result.stdout.strip()}")
        if result.stderr:
            print(f"     {YELLOW}STDERR:{RESET} {result.stderr.strip()}")
            
        return result
        
    except subprocess.CalledProcessError as e:
        print(f"\n{RED}*{RESET} ERROR: Comando falhou (Código: {e.returncode})", file=sys.stderr)
        print(f"{RED}  Comando:{RESET} {' '.join(e.cmd)}", file=sys.stderr)
        print(f"{RED}  Stdout:{RESET} {e.stdout}", file=sys.stderr)
        print(f"{RED}  Stderr:{RESET} {e.stderr}", file=sys.stderr)
        # Re-levanta a exceção para parar o script
        raise
    except Exception as e:
        print(f"\n{RED}*{RESET} ERROR: Falha inesperada ao executar subprocesso: {e}", file=sys.stderr)
        raise

def get_disk_details_from_xml(dom, target_devs_list):
    """
    Analisa o XML da VM e extrai o caminho de origem (source file)
    para cada disco de destino (target dev) solicitado.
    """
    print(f"{GREEN}*{RESET} INFO: Analisando XML para os discos: {CYAN}{', '.join(target_devs_list)}{RESET}")
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
                    print(f"{YELLOW}*{RESET} ATTENTION: Disco {CYAN}{target_name}{RESET} encontrado, mas não possui 'source file'. Ignorando.")
                    continue
                    
                driver = device.find('driver')
                details[target_name] = {
                    'path': source.get('file'),
                    'driver_type': driver.get('type') if driver is not None else 'desconhecido'
                }
                print(f"  -> Encontrado '{CYAN}{target_name}{RESET}': {details[target_name]['path']}")
                target_set.remove(target_name) # Otimização
    
    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Falha ao analisar o XML da VM: {e}", file=sys.stderr)
        return None

    if len(target_set) > 0:
        print(f"{RED}*{RESET} ERROR: Não foi possível encontrar os seguintes discos no XML da VM: {CYAN}{', '.join(target_set)}{RESET}", file=sys.stderr)
        return None

    return details

def manage_retention(backup_dir, retention_days):
    """
    Limpa backups antigos no diretório com base na idade (dias).
    """
    print(f"\n{GREEN}*{RESET} INFO: Verificando política de retenção...")
    print(f"  -> Regra: Reter backups por no máximo {CYAN}{retention_days}{RESET} dias.")

    if not os.path.isdir(backup_dir):
        print(f"{YELLOW}*{RESET} INFO: Diretório de backup ainda não existe. Pulando retenção.")
        return

    now = datetime.now()
    cutoff_date = now - timedelta(days=retention_days)
    
    try:
        files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) 
                 if os.path.isfile(os.path.join(backup_dir, f)) and f.endswith('.bak')]
        
        if not files:
            print(f"{GREEN}*{RESET} INFO: Nenhum backup antigo (.bak) encontrado.")
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
                print(f"  -> {YELLOW}Retenção (Idade):{RESET} Marcado para remoção (expirado): {CYAN}{os.path.basename(f)}{RESET}")
                for_removal.add(f)
            else:
                kept_backups.append(f)

        if for_removal:
            print(f"{YELLOW}*{RESET} ATTENTION: Removendo backups antigos...")
            for f in for_removal:
                try:
                    os.remove(f)
                    print(f"    -> {RED}Removido:{RESET} {os.path.basename(f)}")
                except OSError as e:
                    print(f"{RED}*{RESET} ERROR: Falha ao remover {CYAN}{f}{RESET}: {e}", file=sys.stderr)
        else:
            print(f"{GREEN}*{RESET} INFO: Nenhum backup para remover.")

        print(f"\n{GREEN}*{RESET} INFO: Backups mantidos (existentes):")
        if not kept_backups:
            print("    -> Nenhum backup existente foi mantido.")
        else:
            for f in kept_backups:
                print(f"    -> {os.path.basename(f)}")

    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Falha ao processar retenção: {e}", file=sys.stderr)


def check_available_space(backup_dir, disk_details):
    """
    Verifica se há espaço suficiente no destino para o backup,
    incluindo uma margem de segurança.
    """
    print(f"\n{GREEN}*{RESET} INFO: Verificando espaço em disco...")
    
    try:
        total_size_needed = 0
        for dev, info in disk_details.items():
            try:
                disk_size = os.path.getsize(info['path'])
                total_size_needed += disk_size
                print(f"  -> Disco '{CYAN}{dev}{RESET}' ({info['path']}) requer {disk_size / (1024**3):.2f} GB")
            except OSError as e:
                raise Exception(f"Falha ao obter tamanho do disco {dev} em {info['path']}: {e}")

        final_size_needed = total_size_needed * (1 + SAFETY_MARGIN_PERCENT)
        
        os.makedirs(os.path.dirname(backup_dir), exist_ok=True)
        
        usage = shutil.disk_usage(backup_dir)
        available_space = usage.free

        print(f"  -> Tamanho total (origem): {total_size_needed / (1024**3):.2f} GB")
        print(f"  -> Necessário (com margem): {final_size_needed / (1024**3):.2f} GB")
        print(f"  -> Disponível (destino):    {available_space / (1024**3):.2f} GB")

        if final_size_needed > available_space:
            raise Exception("Espaço insuficiente no dispositivo de backup.")
            
        print(f"  -> {GREEN}Espaço suficiente verificado.{RESET}")
        return True

    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Na verificação de espaço: {e}", file=sys.stderr)
        return False

# --- LÓGICA DE BACKUP (MODO LIBVIRT API) ---

def run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp, retention_days):
    """
    Executa o backup usando a API nativa dom.backupBegin()
    """
    
    # Executa a retenção APENAS neste modo
    manage_retention(backup_dir, retention_days)
    
    print(f"\n{GREEN}*{RESET} INFO: [Modo Libvirt] Gerando XML de backup...")
    
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
            print(f"  -> Incluindo disco '{CYAN}{target_dev}{RESET}' para -> {CYAN}{backup_file_path}{RESET}")

        backup_xml_parts.append("</disks></domainbackup>")
        backup_xml = "".join(backup_xml_parts)
        
        print(f"\n{GREEN}*{RESET} INFO: [Modo Libvirt] Iniciando Backup Live...")
        start_time = time.time()

        dom.backupBegin(backup_xml, None, 0)
        backup_started = True 
        
        job_mode_reported = False
        spinner_chars = ['|', '/', '-', '\\']
        spinner_index = 0
        
        while True:
            job_info = dom.jobInfo()
            elapsed_time = time.time() - start_time
            
            try:
                job_type = job_info.type
                data_total = job_info.dataTotal
                
                if not job_mode_reported:
                    print(f"\n  -> {GREEN}Modo de job detectado:{RESET} Moderno (Objeto)")
                    job_mode_reported = True
                    
            except AttributeError:
                if not job_mode_reported:
                    print(f"\n  -> {YELLOW}Modo de job detectado:{RESET} Legado (Lista/Tupla)")
                    job_mode_reported = True
                
                job_type = job_info[JOB_INFO_TYPE_INDEX]
                data_total = job_info[JOB_INFO_TOTAL_INDEX]
                
            spinner_char = spinner_chars[spinner_index % len(spinner_chars)]
            spinner_index += 1
            total_mb = data_total / 1048576
            print(f"{CYAN}Progresso:{RESET} [{GREEN}{spinner_char}{RESET}] (Aguardando {CYAN}{total_mb:.0f} MB{RESET}... {elapsed_time:.1f}s)", end='\r')

            if job_type == libvirt.VIR_DOMAIN_JOB_NONE:
                end_time = time.time()
                time_elapsed_min = (end_time - start_time) / 60
                
                print(" " * 80, end='\r') 
                
                print(f"\n{GREEN}=================================================={RESET}")
                print(f"{GREEN}Backup concluído com sucesso! (Modo Libvirt){RESET}")
                print(f"Tempo total: {time_elapsed_min:.2f} minutos")
                print("Arquivos Gerados:")
                for dev, path in backup_files_map.items():
                    print(f"  -> Disco {CYAN}{dev}{RESET}: {path}")
                print(f"{GREEN}=================================================={RESET}")
                break
            
            time.sleep(1)
            
    except libvirt.libvirtError as e:
        print(f"\n{RED}*{RESET} ERROR: Erro na Libvirt: {e}", file=sys.stderr)
        if "cannot acquire state change lock" not in str(e):
            if backup_started:
                print(f"{YELLOW}*{RESET} ATTENTION: Tentando abortar o job preso via CLI para a próxima execução...")
                try:
                    subprocess.run(['virsh', 'domjobabort', domain_name], 
                                   check=True, 
                                   capture_output=True, 
                                   text=True)
                    print(f"  -> {GREEN}Comando 'virsh domjobabort' enviado.{RESET}")
                except Exception as e_abort:
                    print(f"{RED}*{RESET} ERROR: Falha ao tentar domjobabort: {e_abort.stderr or e_abort}", file=sys.stderr)
        # Propaga o erro para o handler principal
        raise
    
    except Exception as e:
        print(f"\n{RED}*{RESET} ERROR: Erro inesperado no [Modo Libvirt]: {e}", file=sys.stderr)
        # Propaga o erro para o handler principal
        raise
        
    finally:
        # A limpeza de arquivos parciais (em caso de Ctrl+C) é tratada no bloco principal
        pass


# --- LÓGICA DE BACKUP (MODO SNAPSHOT + RSYNC) ---

def run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp):
    """
    Executa o backup usando a abordagem Snapshot + Rsync + Blockcommit.
    A RETENÇÃO NÃO É APLICADA AQUI.
    """
    print(f"\n{GREEN}*{RESET} INFO: [Modo Snapshot] Iniciando backup via Snapshot + Rsync...")
    
    domain_name = dom.name()
    snapshot_name = f"backup_snap_{timestamp}"
    
    # Mapa para rastrear todos os arquivos envolvidos
    # (disco_alvo) -> {'base': path, 'snap': path, 'bak': path}
    snapshot_files_map = {}
    
    snapshot_created = False
    
    try:
        # --- Preparar Comandos ---
        
        # Comando para criar o snapshot (PASSO 1)
        cmd_snapshot = [
            'virsh', 'snapshot-create-as', 
            '--domain', domain_name, 
            '--name', snapshot_name,
            '--disk-only', '--atomic', '--quiesce'
        ]
        
        cmds_rsync = []     # Lista de comandos rsync (PASSO 2)
        cmds_commit = []    # Lista de comandos blockcommit (PASSO 3)
        
        print(f"{GREEN}*{RESET} INFO: [Modo Snapshot] Preparando especificações de disco...")

        for target_dev, info in disk_details.items():
            base_path = info['path']
            
            # Define o nome do arquivo de snapshot temporário (ex: /path/vm.qcow2.backup_snap_20251112.qcow2)
            snap_path = f"{base_path}.{snapshot_name}.qcow2"
            
            # Define o nome do arquivo de backup final (ex: /backup/vm/vm-vda-20251112.qcow2.bak)
            bak_filename = f"{domain_name}-{target_dev}-{timestamp}.{DISK_FORMAT}.bak"
            bak_path = os.path.join(backup_dir, bak_filename)
            
            # Armazena para limpeza em caso de falha
            snapshot_files_map[target_dev] = {'base': base_path, 'snap': snap_path, 'bak': bak_path}
            
            # Adiciona a especificação do disco ao comando de snapshot
            # (Diz ao libvirt para criar o snap_path como o novo disco ativo para vda)
            cmd_snapshot.extend(['--diskspec', f"{target_dev},file={snap_path},driver=qcow2"])
            
            # Prepara o comando rsync (base congelada -> destino de backup)
            cmds_rsync.append({
                'dev': target_dev, 
                'cmd': ['rsync', '-avh', '--progress', '--inplace', base_path, bak_path]
            })
            
            # Prepara o comando blockcommit (mescla o snapshot de volta na base)
            cmds_commit.append({
                'dev': target_dev,
                'cmd': ['virsh', 'blockcommit', domain_name, target_dev, '--active', '--pivot', '--verbose']
            })
            
            print(f"  -> Disco {CYAN}{target_dev}{RESET}:")
            print(f"     Base (Origem): {base_path}")
            print(f"     Snap (Temp):   {snap_path}")
            print(f"     Backup (Dest): {bak_path}")

        # --- Executar Comandos ---

        start_time = time.time()

        # PASSO 1: Criar o snapshot
        print(f"\n{GREEN}*{RESET} INFO: [Modo Snapshot] PASSO 1: Criando snapshot '{CYAN}{snapshot_name}{RESET}'...")
        run_subprocess(cmd_snapshot)
        snapshot_created = True
        print(f"  -> {GREEN}Snapshot criado.{RESET} Os discos base estão congelados.")

        # PASSO 2: Fazer backup (rsync)
        print(f"\n{GREEN}*{RESET} INFO: [Modo Snapshot] PASSO 2: Executando rsync dos discos base...")
        for item in cmds_rsync:
            print(f"  -> Sincronizando disco {CYAN}{item['dev']}{RESET}...")
            run_subprocess(item['cmd'])
            print(f"  -> {GREEN}Sincronização de {item['dev']} concluída.{RESET}")
        
        # PASSO 3: Blockcommit (Pivot)
        print(f"\n{GREEN}*{RESET} INFO: [Modo Snapshot] PASSO 3: Executando blockcommit (pivot) para mesclar dados...")
        for item in cmds_commit:
            print(f"  -> Mesclando disco {CYAN}{item['dev']}{RESET}...")
            run_subprocess(item['cmd'])
            print(f"  -> {GREEN}Mesclagem de {item['dev']} concluída.{RESET}")
            
        # PASSO 4: Limpar metadados do snapshot
        print(f"\n{GREEN}*{RESET} INFO: [Modo Snapshot] PASSO 4: Removendo metadados do snapshot...")
        cmd_snap_delete = ['virsh', 'snapshot-delete', domain_name, snapshot_name, '--metadata']
        run_subprocess(cmd_snap_delete)
        
        end_time = time.time()
        time_elapsed_min = (end_time - start_time) / 60

        print(f"\n{GREEN}=================================================={RESET}")
        print(f"{GREEN}Backup concluído com sucesso! (Modo Snapshot){RESET}")
        print(f"Tempo total: {time_elapsed_min:.2f} minutos")
        print(f"{GREEN}INFO:{RESET} Os arquivos de snapshot temporários (ex: *.qcow2.snap) são removidos automaticamente pelo libvirt após o blockcommit.")
        print("Arquivos Gerados:")
        for dev, paths in snapshot_files_map.items():
            print(f"  -> Disco {CYAN}{dev}{RESET}: {paths['bak']}")
        print(f"{GREEN}=================================================={RESET}")

    except Exception as e:
        print(f"\n{RED}*{RESET} ERROR: Falha catastrófica durante o backup (Modo Snapshot).", file=sys.stderr)
        
        if not snapshot_created:
            print(f"{YELLOW}*{RESET} ATTENTION: Falha ocorreu ANTES da criação do snapshot.")
            print(f"{GREEN}*{RESET} INFO: O estado da VM deve estar normal. Nenhum snapshot foi criado.")
        else:
            print(f"{RED}******************************************************")
            print(f"{RED}* ATTENTION: FALHA CRÍTICA APÓS CRIAÇÃO DO SNAPSHOT *")
            print(f"{RED}******************************************************")
            print(f"{YELLOW}  A VM PODE ESTAR EM ESTADO INCONSISTENTE.")
            print(f"{YELLOW}  O snapshot '{CYAN}{snapshot_name}{RESET}' PODE AINDA ESTAR ATIVO.")
            print(f"{YELLOW}  Os discos podem estar apontando para:{RESET}")
            for dev, paths in snapshot_files_map.items():
                 print(f"{YELLOW}    -> {dev}: {paths['snap']}{RESET}")
            print(f"{RED}  AÇÃO MANUAL É PROVAVELMENTE NECESSÁRIA.")
            print(f"{YELLOW}  Verifique com 'virsh domblklist {domain_name}' e 'virsh snapshot-list {domain_name}'.")
            print(f"{YELLOW}  Pode ser necessário executar 'virsh blockcommit' e 'virsh snapshot-delete' manualmente.{RESET}")

        # Limpa arquivos .bak parciais que podem ter sido criados
        print(f"\n{YELLOW}*{RESET} ATTENTION: Removendo arquivos de backup (.bak) parciais desta execução...")
        for dev, paths in snapshot_files_map.items():
            if os.path.exists(paths['bak']):
                try:
                    os.remove(paths['bak'])
                    print(f"    -> {RED}Removido:{RESET} {os.path.basename(paths['bak'])}")
                except OSError as e_rm:
                    print(f"{RED}*{RESET} ERROR: Falha ao remover {CYAN}{paths['bak']}{RESET}: {e_rm}", file=sys.stderr)
        
        # Propaga o erro para o handler principal
        raise


# --- FUNÇÃO PRINCIPAL ---

def run_backup(domain_name, backup_base_dir, disk_targets, retention_days, backup_mode):
    
    conn = None
    dom = None
    
    try:
        print(f"{GREEN}*{RESET} INFO: Conectando ao hypervisor em: {CYAN}{CONNECT_URI}{RESET}")
        conn = libvirt.open(CONNECT_URI)
        if conn is None:
            raise Exception(f"Falha ao abrir conexão com o hypervisor em {CONNECT_URI}")

        print(f"\n{CYAN}--- Diagnóstico de Versão ---{RESET}")
        try:
            py_ver = importlib.metadata.version('libvirt-python')
            print(f"  -> {CYAN}Versão libvirt-python:{RESET} {py_ver}")
        except importlib.metadata.PackageNotFoundError:
            print(f"  -> {YELLOW}Versão libvirt-python:{RESET} Não encontrada via metadata.")

        try:
            daemon_ver_int = conn.getVersion()
            major = daemon_ver_int // 1000000
            minor = (daemon_ver_int % 1000000) // 1000
            release = daemon_ver_int % 1000
            print(f"  -> {CYAN}Versão libvirt-daemon (serviço):{RESET} {major}.{minor}.{release}")
        except Exception as e:
            print(f"  -> {YELLOW}Versão libvirt-daemon (serviço):{RESET} Falha ao obter ({e})")
        print(f"{CYAN}-------------------------------{RESET}")

        try:
            dom = conn.lookupByName(domain_name)
            print(f"{GREEN}*{RESET} INFO: Domínio '{CYAN}{domain_name}{RESET}' encontrado.")
        except libvirt.libvirtError:
            print(f"{RED}*{RESET} ERROR: Domínio '{CYAN}{domain_name}{RESET}' não encontrado.", file=sys.stderr)
            sys.exit(1)

        # --- VERIFICAÇÃO DE JOB PRESO ---
        print(f"\n{GREEN}*{RESET} INFO: Verificando se há jobs de backup presos...")
        try:
            job_info = dom.jobInfo()
            
            try:
                job_type = job_info.type
            except AttributeError:
                job_type = job_info[JOB_INFO_TYPE_INDEX]

            if job_type != libvirt.VIR_DOMAIN_JOB_NONE:
                print(f"{YELLOW}*{RESET} ATTENTION: Um job (tipo {job_type}) já está em execução para este domínio.")
                print(f"{YELLOW}*{RESET} ATTENTION: Tentando abortar o job anterior (via CLI) para iniciar o novo backup...")
                
                try:
                    subprocess.run(['virsh', 'domjobabort', domain_name], 
                                   check=True, 
                                   capture_output=True, 
                                   text=True)
                    print(f"  -> {GREEN}Comando 'virsh domjobabort' enviado.{RESET}")
                    print(f"  -> {CYAN}Aguardando 3s para o job ser limpo...{RESET}")
                    time.sleep(3) 
                except Exception as e_abort_cli:
                    error_output = e_abort_cli.stderr if hasattr(e_abort_cli, 'stderr') else str(e_abort_cli)
                    print(f"{RED}*{RESET} ERROR: Falha ao tentar 'virsh domjobabort': {error_output}", file=sys.stderr)
                    print(f"{RED}*{RESET} ERROR: O backup não pode continuar.")
                    sys.exit(1)
                    
            else:
                print(f"  -> {GREEN}Nenhum job ativo encontrado. O backup pode prosseguir.{RESET}")

        except libvirt.libvirtError as e_jobinfo:
            print(f"{RED}*{RESET} ERROR: Falha ao verificar informações do job: {e_jobinfo}", file=sys.stderr)
            sys.exit(1)
        # --- [FIM] VERIFICAÇÃO DE JOB PRESO ---

        backup_dir = os.path.join(backup_base_dir, domain_name)
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # A retenção foi movida para dentro do 'run_backup_libvirt_api'

        disk_details = get_disk_details_from_xml(dom, disk_targets)
        if disk_details is None:
            raise Exception("Falha ao obter detalhes dos discos. Verifique os logs acima.")
            
        if not check_available_space(backup_dir, disk_details):
            sys.exit(1)

        # --- ROTEADOR DO MODO DE BACKUP ---
        
        if backup_mode == 'libvirt':
            run_backup_libvirt_api(dom, backup_dir, disk_details, timestamp, retention_days)
        
        elif backup_mode == 'snapshot':
            run_backup_snapshot_rsync(dom, backup_dir, disk_details, timestamp)
        
        else:
            # Isso não deve acontecer devido ao 'choices' do argparse
            raise Exception(f"Modo de backup desconhecido: {backup_mode}")


    except KeyboardInterrupt:
        print(f"\n{RED}*{RESET} INTERRUPÇÃO: Script interrompido pelo usuário (Ctrl+C).")
        
        # A lógica de limpeza específica do modo (abortar job, remover arquivos)
        # é tratada dentro das próprias funções 'run_backup_*' ou nos seus 'except'
        
        # O modo 'snapshot' limpa seus próprios arquivos .bak no 'except'
        # O modo 'libvirt' precisa de uma verificação aqui
        
        if backup_mode == 'libvirt' and dom is not None:
             print(f"{YELLOW}*{RESET} ATTENTION: Tentando abortar o job de backup (via CLI)...")
             try:
                 subprocess.run(['virsh', 'domjobabort', domain_name], 
                                check=True, 
                                capture_output=True, 
                                text=True)
                 print(f"  -> {GREEN}Comando 'virsh domjobabort' enviado.{RESET}")
             except Exception as e_abort_cli:
                 error_output = e_abort_cli.stderr if hasattr(e_abort_cli, 'stderr') else str(e_abort_cli)
                 print(f"{RED}*{RESET} ERROR: Falha ao tentar domjobabort: {error_output}", file=sys.stderr)
        
        print(f"{RED}*{RESET} Script encerrado.")
        sys.exit(130) 

    except Exception as e:
        print(f"\n{RED}*{RESET} ERROR: Erro inesperado na execução principal: {e}", file=sys.stderr)
        sys.exit(1)
        
    finally:
        if conn:
            conn.close()
            print(f"\n{GREEN}*{RESET} INFO: Conexão com o hypervisor fechada.")

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
    
    args = parser.parse_args()
    
    run_backup(args.domain, args.backup_dir, args.disk, args.retention_days, args.mode)