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

def manage_retention(backup_dir, retention_days, retention_count):
    """
    Limpa backups antigos no diretório com base nas regras de retenção
    e lista os backups que serão mantidos.
    """
    print(f"\n{GREEN}*{RESET} INFO: Verificando política de retenção...")
    print(f"  -> Regra: Manter no máximo {CYAN}{retention_count}{RESET} backups.")
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

        num_to_remove_by_count = max(0, len(backups) - (retention_count - 1))
        
        backups_to_remove_by_count = backups[:num_to_remove_by_count]
        backups_to_check_by_age = backups[num_to_remove_by_count:]

        for mtime, f in backups_to_remove_by_count:
            print(f"  -> {YELLOW}Retenção (Contagem):{RESET} Marcado para remoção (excesso): {CYAN}{os.path.basename(f)}{RESET}")
            for_removal.add(f)
        
        for mtime, f in backups_to_check_by_age:
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
        print(f"  -> Disponível (destino):   {available_space / (1024**3):.2f} GB")

        if final_size_needed > available_space:
            raise Exception("Espaço insuficiente no dispositivo de backup.")
            
        print(f"  -> {GREEN}Espaço suficiente verificado.{RESET}")
        return True

    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Na verificação de espaço: {e}", file=sys.stderr)
        return False

# --- FUNÇÃO PRINCIPAL ---

def run_backup(domain_name, backup_base_dir, disk_targets, retention_days, retention_count, limit_mb):
    
    conn = None
    dom = None
    backup_started = False
    
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

        # --- APLICAR LIMITE DE I/O (blkiotune) ---
        if limit_mb > 0:
            limit_bytes = limit_mb * 1024 * 1024
            params = {'read_bytes_sec': limit_bytes} 
            print(f"\n{GREEN}*{RESET} INFO: Aplicando I/O read limit de {CYAN}{limit_mb} MB/s{RESET} aos discos: {CYAN}{', '.join(disk_targets)}{RESET}...")
            
            # --- Início EAFP para APLICAR limite ---
            try:
                # Tenta a chamada moderna (com 'flags=0')
                for disk in disk_targets:
                    dom.blockIoTune(disk, params, 0)
                print(f"  -> {GREEN}Limite aplicado (Modo Moderno).{RESET}")
            except TypeError:
                # Se falhar, é uma biblioteca antiga. Tenta a chamada legada.
                print(f"  -> {YELLOW}Detectado blkiotune legado. Aplicando (Modo Legado).{RESET}")
                try:
                    for disk in disk_targets:
                        dom.blockIoTune(disk, params)
                    print(f"  -> {GREEN}Limite aplicado (Modo Legado).{RESET}")
                except libvirt.libvirtError as e_legacy:
                    print(f"{RED}*{RESET} ERROR: Falha ao aplicar limite de I/O (Modo Legado). O backup continuará sem limite. Erro: {e_legacy}", file=sys.stderr)
            except libvirt.libvirtError as e:
                print(f"{RED}*{RESET} ERROR: Falha ao aplicar limite de I/O (Modo Moderno). O backup continuará sem limite. Erro: {e}", file=sys.stderr)
            # --- Fim EAFP para APLICAR limite ---
                
        else:
            print(f"\n{YELLOW}*{RESET} ATTENTION: Nenhum limite de I/O foi definido ({CYAN}--limit-mb 0{RESET}).")
            print("  -> O backup será executado na velocidade máxima permitida pelo hardware.")
            print("  -> Isso pode causar alto I/O no host e degradar a performance de outras VMs.")


        backup_dir = os.path.join(backup_base_dir, domain_name)
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        manage_retention(backup_dir, retention_days, retention_count)

        disk_details = get_disk_details_from_xml(dom, disk_targets)
        if disk_details is None:
            raise Exception("Falha ao obter detalhes dos discos. Verifique os logs acima.")
            
        if not check_available_space(backup_dir, disk_details):
            sys.exit(1)

        print(f"\n{GREEN}*{RESET} INFO: Gerando XML de backup...")
        backup_xml_parts = []
        backup_files_map = {}
        
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
        
        print(f"\n{GREEN}*{RESET} INFO: Iniciando Backup Live...")
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
                print(f"{GREEN}Backup concluído com sucesso!{RESET}")
                print(f"Tempo total: {time_elapsed_min:.2f} minutos")
                print("Arquivos Gerados:")
                for dev, path in backup_files_map.items():
                    print(f"  -> Disco {CYAN}{dev}{RESET}: {path}")
                print(f"{GREEN}=================================================={RESET}")
                break
            
            time.sleep(1)

    except libvirt.libvirtError as e:
        print(f"\n{RED}*{RESET} ERROR: Erro na Libvirt: {e}", file=sys.stderr)
        if backup_started:
            print(f"{YELLOW}*{RESET} ATTENTION: Tentando abortar o job preso via CLI para a próxima execução...")
            try:
                subprocess.run(['virsh', 'domjobabort', domain_name, '--async'], 
                               check=True, 
                               capture_output=True, 
                               text=True)
                print(f"  -> {GREEN}Comando 'virsh domjobabort' enviado.{RESET}")
            except Exception as e_abort:
                 print(f"{RED}*{RESET} ERROR: Falha ao tentar domjobabort: {e_abort.stderr or e_abort}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}*{RESET} ERROR: Erro inesperado: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        # --- REMOVER LIMITE DE I/O (SEMPRE) ---
        if dom is not None and limit_mb > 0:
            print(f"\n{GREEN}*{RESET} INFO: Removendo limite de I/O...")
            params_unlimited = {'read_bytes_sec': 0}
            
            # --- Início EAFP para REMOVER limite ---
            try:
                # Tenta a chamada moderna
                for disk in disk_targets:
                    dom.blockIoTune(disk, params_unlimited, 0)
                print(f"  -> {GREEN}Limite removido (Modo Moderno).{RESET}")
            except TypeError:
                # Se falhar, tenta a chamada legada
                print(f"  -> {YELLOW}Detectado blkiotune legado. Removendo (Modo Legado).{RESET}")
                try:
                    for disk in disk_targets:
                        dom.blockIoTune(disk, params_unlimited)
                    print(f"  -> {GREEN}Limite removido (Modo Legado).{RESET}")
                except Exception as e_legacy:
                    print(f"{RED}*{RESET} ERROR: Falha ao remover limite de I/O (Modo Legado): {e_legacy}", file=sys.stderr)
            except Exception as e:
                print(f"{RED}*{RESET} ERROR: Falha ao remover limite de I/O (Modo Moderno): {e}", file=sys.stderr)
            # --- Fim EAFP para REMOVER limite ---

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
                        help="Reter backups por no máximo X dias (Padrão: 7).")
                        
    parser.add_argument('--retention-count',
                        type=int,
                        default=7,
                        help="Manter no máximo X backups (Padrão: 7).")
                        
    parser.add_argument('--limit-mb',
                        type=int,
                        default=0,
                        help="Limitar a velocidade de leitura do backup em MB/s (Padrão: 0 = ilimitado).")
    
    args = parser.parse_args()
    
    run_backup(args.domain, args.backup_dir, args.disk, args.retention_days, args.retention_count, args.limit_mb)