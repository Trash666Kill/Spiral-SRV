#!/usr/bin/env python3
import libvirt
import sys
import os
import shutil
import time
import argparse
from datetime import datetime
import xml.etree.ElementTree as ET

# --- CONSTANTES ---
TARGET_DISK_BUS_NAME = 'vda'
DISK_FORMAT = 'qcow2'
CONNECT_URI = 'qemu:///system'
SAFETY_MARGIN_PERCENT = 0.10    
BACKUP_RETENTION_DAYS = 7
BACKUP_RETENTION_COUNT = 7

# CORREÇÃO: Usamos o valor numérico conhecido para compatibilidade máxima
JOB_TYPE_NONE_VALUE = 0

# Índices para o acesso de tupla (Fallback forçado, embora você tenha instalado o moderno)
JOB_INFO_TYPE_INDEX = 0
JOB_INFO_PROCESSED_INDEX = 2
JOB_INFO_TOTAL_INDEX = 4

# --- UTILS (mantidos) ---
def get_disk_paths(dom):
    raw_xml = dom.XMLDesc(0)
    root = ET.fromstring(raw_xml)
    for device in root.findall('./devices/disk'):
        target = device.find('target')
        source = device.find('source')
        if target is not None and target.get('dev') == TARGET_DISK_BUS_NAME:
            if source is not None and source.get('file'):
                return source.get('file')
            break
    return None

def get_disk_info(file_path):
    return os.path.getsize(file_path)

def get_available_space_mb(path):
    total_b, used_b, free_b = shutil.disk_usage(path)
    return free_b / (1024 * 1024)

# --- FUNÇÃO PRINCIPAL ---

def run_backup(domain_name, backup_base_dir):
    
    conn = None
    dom = None
    backup_started = False
    
    try:
        # 1. Conexão com Libvirt
        print(f"🔗 Conectando ao hypervisor em: {CONNECT_URI}")
        conn = libvirt.open(CONNECT_URI)
        if conn is None:
            raise Exception(f"Falha ao abrir conexão com o hypervisor em {CONNECT_URI}")

        # 2-5. [Configuração, Verificações e Retenção] (Omitido por brevidade)
        backup_dir = os.path.join(backup_base_dir, domain_name)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"{domain_name}-{timestamp}.{DISK_FORMAT}.bak"
        backup_file_path = os.path.join(backup_dir, backup_filename)

        try:
            dom = conn.lookupByName(domain_name)
        except libvirt.libvirtError:
            print(f"❌ ERRO: Domínio '{domain_name}' não encontrado.")
            sys.exit(1)
            
        # ... (Resto das verificações de caminho, espaço, e retenção)
        
        # 6. Iniciar Backup com libvirt.virDomainBackupBegin()
        backup_xml = f"""
<domainbackup>
  <disks>
    <disk name='{TARGET_DISK_BUS_NAME}' type='file'>
      <target file='{backup_file_path}'/>
      <driver type='{DISK_FORMAT}'/>
    </disk>
  </disks>
</domainbackup>
"""
        print("\n🚀 Iniciando Backup Live...")
        start_time = time.time()

        dom.backupBegin(backup_xml, None, 0)
        backup_started = True 
        
        # 7. Monitoramento do Job de Backup (USANDO VALOR NUMÉRICO PARA JOB_TYPE)
        while True:
            job_info = dom.jobInfo()
            
            # Lógica de compatibilidade (forçada)
            if isinstance(job_info, tuple) or isinstance(job_info, list):
                job_type = job_info[JOB_INFO_TYPE_INDEX]
                data_processed = job_info[JOB_INFO_PROCESSED_INDEX]
                data_total = job_info[JOB_INFO_TOTAL_INDEX]
            else:
                job_type = job_info.type
                data_processed = job_info.dataProcessed
                data_total = job_info.dataTotal

            # Verifica se o job terminou usando o valor numérico 0
            if job_type == JOB_TYPE_NONE_VALUE:
                end_time = time.time()
                time_elapsed_min = (end_time - start_time) / 60
                
                print("\n==================================================")
                print("✅ Backup concluído com sucesso!")
                print(f"⏱️ Tempo total: {time_elapsed_min:.2f} minutos")
                print(f"💾 Caminho Absoluto: {backup_file_path}")
                print("==================================================")
                break
            
            # Exibe o progresso
            if data_total > 0:
                progress_percent = (data_processed / data_total) * 100
                print(f"Progresso: {progress_percent:.2f}% ({data_processed/1048576:.0f} MB / {data_total/1048576:.0f} MB)", end='\r')
            
            time.sleep(10)

    except libvirt.libvirtError as e:
        print(f"\n❌ ERRO na Libvirt: {e}")
        # Tenta abortar o job usando a CLI para limpar o lock, já que backupEnd falha.
        # Isso garante que a próxima tentativa não falhe com 'state change lock'.
        print("Tentando abortar o job preso via CLI para a próxima execução...")
        try:
            subprocess.run(['virsh', 'domjobabort', domain_name], check=True, capture_output=True)
        except Exception as e_abort:
             print(f"⚠️ AVISO: Falha ao tentar domjobabort: {e_abort}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERRO inesperado: {e}")
        sys.exit(1)
    finally:
        # Apenas fecha a conexão, já que backupEnd falha
        if conn:
            conn.close()

# --- EXECUÇÃO (Omitido por brevidade) ---

if __name__ == "__main__":
    import subprocess # Adicionar esta importação
    parser = argparse.ArgumentParser(description="Script de backup live para VM KVM/QEMU (compatibilidade forçada).")
    parser.add_argument('--domain', required=True, help="Nome do domínio (VM) a ser feito o backup, e.g., 'win10'.")
    parser.add_argument('--backup-dir', required=True, help="Diretório base onde os backups serão armazenados, e.g., '/home/sysop/.virt/'.")
    
    args = parser.parse_args()
    
    # IMPORTANTE: Re-adicione o código de verificação de espaço e retenção aqui,
    # ou mova a lógica para dentro de run_backup como nos exemplos anteriores.
    
    # Adicionando a importação do subprocess para garantir que a correção de erro funcione
    if 'subprocess' not in sys.modules:
        import subprocess
        
    run_backup(args.domain, args.backup_dir)