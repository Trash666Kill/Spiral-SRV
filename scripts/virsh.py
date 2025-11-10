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

# --- CONSTANTES ---
DISK_FORMAT = 'qcow2'
CONNECT_URI = 'qemu:///system'
SAFETY_MARGIN_PERCENT = 0.10
BACKUP_RETENTION_DAYS = 7
BACKUP_RETENTION_COUNT = 7

# Constantes de índice para o modo legado (Fallback)
JOB_INFO_TYPE_INDEX = 0
JOB_INFO_PROCESSED_INDEX = 2 # Não mais usado para progresso, mas mantido
JOB_INFO_TOTAL_INDEX = 4


# --- UTILS ---

def get_disk_details_from_xml(dom, target_devs_list):
    """
    Analisa o XML da VM e extrai o caminho de origem (source file)
    para cada disco de destino (target dev) solicitado.
    """
    print(f"Analisando XML para os discos: {', '.join(target_devs_list)}")
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
                    print(f"AVISO: Disco {target_name} encontrado, mas não possui 'source file'. Ignorando.")
                    continue
                    
                driver = device.find('driver')
                details[target_name] = {
                    'path': source.get('file'),
                    'driver_type': driver.get('type') if driver is not None else 'desconhecido'
                }
                print(f"  -> Encontrado '{target_name}': {details[target_name]['path']}")
                target_set.remove(target_name) # Otimização
    
    except Exception as e:
        print(f"ERRO ao analisar o XML da VM: {e}")
        return None

    if len(target_set) > 0:
        print(f"ERRO: Não foi possível encontrar os seguintes discos no XML da VM: {', '.join(target_set)}")
        return None

    return details

def manage_retention(backup_dir):
    """
    Limpa backups antigos no diretório com base nas constantes
    BACKUP_RETENTION_DAYS e BACKUP_RETENTION_COUNT.
    """
    print("\nVerificando política de retenção...")
    if not os.path.isdir(backup_dir):
        print("  -> Diretório de backup ainda não existe. Pulando retenção.")
        return

    now = datetime.now()
    cutoff_date = now - timedelta(days=BACKUP_RETENTION_DAYS)
    
    try:
        files = [os.path.join(backup_dir, f) for f in os.listdir(backup_dir) 
                 if os.path.isfile(os.path.join(backup_dir, f)) and f.endswith('.bak')]
        
        if not files:
            print("  -> Nenhum backup antigo (.bak) encontrado.")
            return

        backups = []
        for f in files:
            try:
                backups.append((os.path.getmtime(f), f))
            except OSError:
                continue 
                
        backups.sort()

        for_removal = set()

        num_to_remove_by_count = max(0, len(backups) - (BACKUP_RETENTION_COUNT - 1))
        if num_to_remove_by_count > 0:
            for mtime, f in backups[:num_to_remove_by_count]:
                print(f"  -> Retenção (Contagem): Marcado para remoção (muito antigo): {os.path.basename(f)}")
                for_removal.add(f)
        
        for mtime, f in backups:
            if datetime.fromtimestamp(mtime) < cutoff_date:
                print(f"  -> Retenção (Idade): Marcado para remoção (expirado): {os.path.basename(f)}")
                for_removal.add(f)

        if not for_removal:
            print("  -> Nenhum backup para remover.")
            
        for f in for_removal:
            try:
                os.remove(f)
                print(f"    -> Removido: {os.path.basename(f)}")
            except OSError as e:
                print(f"AVISO: Falha ao remover {f}: {e}")

    except Exception as e:
        print(f"AVISO: Falha ao processar retenção: {e}")


def check_available_space(backup_dir, disk_details):
    """
    Verifica se há espaço suficiente no destino para o backup,
    incluindo uma margem de segurança.
    """
    print("\nVerificando espaço em disco...")
    
    try:
        total_size_needed = 0
        for dev, info in disk_details.items():
            try:
                disk_size = os.path.getsize(info['path'])
                total_size_needed += disk_size
                print(f"  -> Disco '{dev}' ({info['path']}) requer {disk_size / (1024**3):.2f} GB")
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
            
        print("  -> Espaço suficiente verificado.")
        return True

    except Exception as e:
        print(f"ERRO na verificação de espaço: {e}")
        return False

# --- FUNÇÃO PRINCIPAL ---

def run_backup(domain_name, backup_base_dir, disk_targets):
    
    conn = None
    dom = None
    backup_started = False
    
    try:
        print(f"Conectando ao hypervisor em: {CONNECT_URI}")
        conn = libvirt.open(CONNECT_URI)
        if conn is None:
            raise Exception(f"Falha ao abrir conexão com o hypervisor em {CONNECT_URI}")

        print("\n--- Diagnóstico de Versão ---")
        try:
            py_ver = importlib.metadata.version('libvirt-python')
            print(f"Versão libvirt-python (pip): {py_ver}")
        except importlib.metadata.PackageNotFoundError:
            print("Versão libvirt-python (pip): Não encontrada via metadata.")

        try:
            daemon_ver_int = conn.getVersion()
            major = daemon_ver_int // 1000000
            minor = (daemon_ver_int % 1000000) // 1000
            release = daemon_ver_int % 1000
            print(f"Versão libvirt-daemon (serviço): {major}.{minor}.{release}")
        except Exception as e:
            print(f"Versão libvirt-daemon (serviço): Falha ao obter ({e})")
        print("-------------------------------")

        try:
            dom = conn.lookupByName(domain_name)
            print(f"Domínio '{domain_name}' encontrado.")
        except libvirt.libvirtError:
            print(f"ERRO: Domínio '{domain_name}' não encontrado.")
            sys.exit(1)

        backup_dir = os.path.join(backup_base_dir, domain_name)
        os.makedirs(backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        manage_retention(backup_dir)

        disk_details = get_disk_details_from_xml(dom, disk_targets)
        if disk_details is None:
            raise Exception("Falha ao obter detalhes dos discos. Verifique os logs acima.")
            
        if not check_available_space(backup_dir, disk_details):
            sys.exit(1)

        print("\nGerando XML de backup...")
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
            print(f"  -> Incluindo disco '{target_dev}' para -> {backup_file_path}")

        backup_xml_parts.append("</disks></domainbackup>")
        backup_xml = "".join(backup_xml_parts)
        
        print("\nIniciando Backup Live...")
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
                # Tenta a forma moderna (assume que job_info é um objeto)
                job_type = job_info.type
                data_total = job_info.dataTotal
                
                if not job_mode_reported:
                    print("\n  -> Modo de job detectado: Moderno (Objeto)")
                    job_mode_reported = True
                    
            except AttributeError:
                # Falhou? Então é a forma antiga (lista/tupla)
                if not job_mode_reported:
                    print("\n  -> Modo de job detectado: Legado (Lista/Tupla)")
                    job_mode_reported = True
                
                job_type = job_info[JOB_INFO_TYPE_INDEX]
                data_total = job_info[JOB_INFO_TOTAL_INDEX]
                
            # --- Lógica de Progresso Unificada (Spinner) ---
            spinner_char = spinner_chars[spinner_index % len(spinner_chars)]
            spinner_index += 1
            total_mb = data_total / 1048576
            print(f"Progresso: [{spinner_char}] (Aguardando {total_mb:.0f} MB... {elapsed_time:.1f}s)", end='\r')


            # Verifica se o job terminou
            if job_type == libvirt.VIR_DOMAIN_JOB_NONE:
                end_time = time.time()
                time_elapsed_min = (end_time - start_time) / 60
                
                # Limpa a linha do spinner antes de imprimir o final
                print(" " * 80, end='\r') 
                
                print("\n==================================================")
                print("Backup concluído com sucesso!")
                print(f"Tempo total: {time_elapsed_min:.2f} minutos")
                print("Arquivos Gerados:")
                for dev, path in backup_files_map.items():
                    print(f"  -> Disco {dev}: {path}")
                print("==================================================")
                break
            
            time.sleep(1) # Sleep de 1 segundo para o spinner

    except libvirt.libvirtError as e:
        print(f"\nERRO na Libvirt: {e}")
        if backup_started:
            print("Tentando abortar o job preso via CLI para a próxima execução...")
            try:
                subprocess.run(['virsh', 'domjobabort', domain_name, '--async'], 
                               check=True, 
                               capture_output=True, 
                               text=True)
                print("  -> Comando 'virsh domjobabort' enviado.")
            except Exception as e_abort:
                 print(f"AVISO: Falha ao tentar domjobabort: {e_abort.stderr or e_abort}")
        sys.exit(1)
    except Exception as e:
        print(f"\nERRO inesperado: {e}")
        sys.exit(1)
    finally:
        if conn:
            conn.close()
            print("\nConexão com o hypervisor fechada.")

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
    
    args = parser.parse_args()
    
    run_backup(args.domain, args.backup_dir, args.disk)