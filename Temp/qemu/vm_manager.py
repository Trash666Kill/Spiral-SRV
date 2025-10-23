#!/usr/bin/env python3

import argparse
import configparser
import os
import random
import shlex
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
import shutil

# --- Constantes de Configuração ---
VMS_DIR = Path("vms")
GLOBAL_CONF = Path("global.conf")
QEMU_BIN = "qemu-system-x86_64"

# --- Conteúdo Padrão para global.conf ---
DEFAULT_GLOBAL_CONF = f"""
;
; Arquivo de configuração global padrão para vm_manager.py
;
; Este arquivo foi gerado automaticamente.
; Por favor, revise os caminhos (especialmente [pools] e [firmware_paths])
; para que correspondam ao seu sistema.
;

[pools]
; O pool de armazenamento padrão se --pool não for usado em 'create'
default = /home/sysop/.virt
Container-A = /mnt/Temp/Container/A/Virt

[network]
; A bridge de rede padrão. (Altere para a sua bridge ex: br0, virbr0)
bridge = br0

[disks]
; O formato de imagem de disco padrão
image_format = qcow2

[hardware]
; O firmware padrão (uefi ou bios)
firmware = uefi
; O chipset padrão (q35 ou i440fx)
chipset = q35

[firmware_paths]
; (IMPORTANTE) Verifique se estes caminhos estão corretos para o seu sistema!
; (Debian/Ubuntu: apt install ovmf)
uefi_code = /usr/share/OVMF/OVMF_CODE_4M.fd
uefi_vars_template = /usr/share/OVMF/OVMF_VARS_4M.fd

[install_defaults_windows]
smp = 4
memory = 4G
disk_size = 64G

[install_defaults_linux]
smp = 2
memory = 2G
disk_size = 8G

[install_defaults_generic]
smp = 2
memory = 1G
disk_size = 16G
"""

def _generate_mac() -> str:
    """Gera um MAC address aleatório com prefixo QEMU (52:54:00)."""
    mac = [0x52, 0x54, 0x00,
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff)]
    return ':'.join(f"{x:02x}" for x in mac)

def get_vm_config(vm_name: str) -> configparser.ConfigParser:
    """Lê o global.conf e o .conf específico da VM em ordem."""
    conf_file = VMS_DIR / f"{vm_name}.conf"
    if not conf_file.exists():
        print(f"Erro: Arquivo de configuração não encontrado: {conf_file}", file=sys.stderr)
        sys.exit(1)

    config = configparser.ConfigParser()
    try:
        config.read([GLOBAL_CONF, conf_file])
        return config
    except configparser.Error as e:
        print(f"Erro ao ler arquivos de configuração: {e}", file=sys.stderr)
        sys.exit(1)

def get_vm_paths(vm_name: str) -> (Path, Path):
    """Retorna os caminhos para os arquivos .pid e .sock da VM."""
    pid_file = VMS_DIR / f"{vm_name}.pid"
    sock_file = VMS_DIR / f"{vm_name}.sock"
    return pid_file, sock_file

def is_vm_running(pid_file: Path) -> bool:
    """Verifica se a VM está rodando com base no arquivo PID."""
    if not pid_file.exists():
        return False
    
    try:
        pid = int(pid_file.read_text())
    except (ValueError, FileNotFoundError):
        return False 

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False 
    except PermissionError:
        return True
    
    return True 

def resolve_image_path(config: configparser.ConfigParser) -> (str, str):
    """Resolve o caminho final do image_file usando a lógica de pools."""
    try:
        image_file = config.get("disks", "image_file")
        image_format = config.get("disks", "image_format")
    except configparser.NoOptionError as e:
        print(f"Erro: Configuração obrigatória '{e.option}' não encontrada na seção [disks].", file=sys.stderr)
        print(f"Dica: Verifique se '{e.option}' está em [disks] no seu global.conf ou vm.conf.", file=sys.stderr)
        sys.exit(1)

    if Path(image_file).is_absolute():
        return image_file, image_format

    try:
        pool_name = config.get("disks", "image_pool", fallback="default")
        pool_path = config.get("pools", pool_name)
    except configparser.NoSectionError:
        print(f"Erro: Seção [pools] não encontrada em {GLOBAL_CONF}", file=sys.stderr)
        sys.exit(1)
    except configparser.NoOptionError:
        print(f"Erro: Pool '{pool_name}' não definido em {GLOBAL_CONF} [pools]", file=sys.stderr)
        sys.exit(1)

    final_path = Path(pool_path) / image_file
    return str(final_path), image_format

def _show_vm_details(vm_name: str):
    """Busca e exibe informações detalhadas de uma única VM."""
    try:
        config = get_vm_config(vm_name)
    except SystemExit:
        return 

    pid_file, sock_file = get_vm_paths(vm_name)
    running = is_vm_running(pid_file)
    
    print(f"--- Detalhes da VM: {vm_name} ---")
    
    status_str = "Rodando" if running else "Parado"
    print(f"\n[Status de Execução]")
    print(f"  Estado:     {status_str}")
    if running:
        try:
            print(f"  PID:        {pid_file.read_text().strip()}")
            print(f"  Monitor:    {sock_file.resolve()}")
        except FileNotFoundError:
            print("  PID/Monitor: (Arquivo desapareceu, limpando...)")
            if pid_file.exists(): pid_file.unlink()
            if sock_file.exists(): sock_file.unlink()
    else:
        print(f"  PID:        N/A")
        print(f"  Monitor:    N/A")

    print(f"\n[Hardware (Configurado)]")
    firmware = config.get('hardware', 'firmware', fallback='bios') 
    chipset = config.get('hardware', 'chipset', fallback='N/A (i440fx)') 
    vm_uuid = config.get('hardware', 'uuid', fallback='N/A')
    os_type = config.get('hardware', 'os_type', fallback='generic') # <-- LÊ OS_TYPE
    print(f"  Memória:    {config.get('hardware', 'memory', fallback='N/A')}")
    print(f"  SMP (vCPUs):{config.get('hardware', 'smp', fallback='N/A')}")
    print(f"  Firmware:   {firmware.upper()}") 
    print(f"  Chipset:    {chipset}") 
    print(f"  UUID:       {vm_uuid}") 
    print(f"  OS Type:    {os_type}") # <-- EXIBE OS_TYPE
    print(f"  CPU (fixo): host")
    print(f"  KVM (fixo): habilitado")

    print(f"\n[Disks (Resolvido)]")
    try:
        image_path, image_format = resolve_image_path(config)
        print(f"  Pool:       {config.get('disks', 'image_pool', fallback='default')}")
        print(f"  Imagem:     {image_path}")
        print(f"  Formato:    {image_format}")
    except Exception as e:
        print(f"  Imagem:     (Erro ao resolver: {e})")

    print(f"\n[Network (Resolvido)]")
    print(f"  Bridge:     {config.get('network', 'bridge', fallback='N/A')}")
    print(f"  MAC:        {config.get('network', 'mac', fallback='N/A')}") 

    print(f"\n[Options (Configurado)]")
    extra_flags = config.get('options', 'extra_flags', fallback="Nenhum")
    print(f"  Flags Extras: {extra_flags}")

def handle_list(args):
    """Lista todas as VMs ou detalhes de uma VM específica."""
    if args.vm_name:
        _show_vm_details(args.vm_name)
        return

    print("VMs definidas:")
    
    vm_files = sorted(list(VMS_DIR.glob("*.conf")))
    if not vm_files:
        print(f"  (Nenhum arquivo .conf encontrado em '{VMS_DIR}/')")
        return

    max_len = max(len(f.stem) for f in vm_files) if vm_files else 0

    for conf_file in vm_files:
        vm_name = conf_file.stem
        pid_file, _ = get_vm_paths(vm_name)
        status = "Rodando" if is_vm_running(pid_file) else "Parado"
        print(f"  - {vm_name:<{max_len}}   ({status})")

def handle_status(args):
    """Verifica e reporta o status de uma VM específica."""
    vm_name = args.vm_name
    pid_file, _ = get_vm_paths(vm_name)
    status = "Rodando" if is_vm_running(pid_file) else "Parado"
    print(f"VM '{vm_name}' está: {status}")

def _build_qemu_command(vm_name: str, config: configparser.ConfigParser, iso_list: list = None, graphical_mode: bool = False) -> list:
    """Função auxiliar interna para construir a lista de comandos QEMU."""
    
    # 1. Resolver caminhos de imagem
    image_path, image_format = resolve_image_path(config)
    
    if not Path(image_path).exists():
        print(f"Erro: Arquivo de imagem não encontrado: {image_path}", file=sys.stderr)
        print("Dica: Use o comando 'create' para criar esta VM e seu disco.", file=sys.stderr)
        sys.exit(1)

    # 2. Montar o comando QEMU base
    qemu_cmd = [
        QEMU_BIN,
        "-enable-kvm",
        "-cpu", "host",
        "-smp", config.get("hardware", "smp", fallback="2"),
        "-m", config.get("hardware", "memory", fallback="2G"),
    ]

    if config.has_option("hardware", "uuid"):
        qemu_cmd.extend(["-uuid", config.get("hardware", "uuid")])

    # 3. Lógica de Chipset
    chipset = config.get("hardware", "chipset", fallback=None)
    if chipset:
        qemu_cmd.extend(["-machine", chipset])

    # --- LÓGICA DE SERIAL (TTY) ---
    os_type = config.get("hardware", "os_type", fallback="generic")
    if os_type == "linux":
        print("Tipo OS 'linux' detectado. Adicionando console serial (-serial pty)...")
        qemu_cmd.extend(["-serial", "pty"])
    # --- FIM DA LÓGICA DE SERIAL ---

    # 4. Lógica de Boot e Gráficos
    qemu_cmd.extend(["-boot", "order=c,menu=on"])

    if graphical_mode:
        print("Modo Gráfico: Iniciando em primeiro plano.")
        if config.has_option("options", "extra_flags"):
             extra_flags = config.get("options", "extra_flags")
             qemu_cmd.extend(shlex.split(extra_flags))
    else:
        # Modo 'start' (headless)
        print("Modo Headless: Iniciando em segundo plano.")
        qemu_cmd.extend(["-vga", "none", "-nographic"])
        pid_file, sock_file = get_vm_paths(vm_name)
        qemu_cmd.extend([
            "-daemonize",
            "-pidfile", str(pid_file),
            "-monitor", f"unix:{sock_file},server,nowait",
        ])

    # 5. Lógica de Firmware (UEFI/BIOS)
    firmware_type = config.get("hardware", "firmware", fallback="bios")
    if firmware_type.lower() == "uefi":
        if not graphical_mode:
            print("Configurando modo UEFI...")
        try:
            code_path = config.get("firmware_paths", "uefi_code")
            vars_template_path = config.get("firmware_paths", "uefi_vars_template")
        except (configparser.NoSectionError, configparser.NoOptionError) as e:
            print(f"Erro: Firmware é 'uefi' mas a seção [firmware_paths] está incompleta ou ausente no {GLOBAL_CONF}", file=sys.stderr)
            sys.exit(1)

        vm_vars_path = VMS_DIR / f"{vm_name}_VARS.fd"
        if not vm_vars_path.exists():
            try:
                # Checa se o template existe ANTES de tentar copiar
                if not Path(vars_template_path).exists():
                    print(f"Erro: Arquivo template UEFI VARS não encontrado: {vars_template_path}", file=sys.stderr)
                    print(f"Verifique o caminho em {GLOBAL_CONF} [firmware_paths]")
                    sys.exit(1)
                print(f"Copiando template UEFI VARS para: {vm_vars_path}")
                shutil.copyfile(vars_template_path, vm_vars_path)
            except Exception as e:
                print(f"Erro ao copiar arquivo VARS UEFI: {e}", file=sys.stderr)
                sys.exit(1)
        
        # Checa se o CODE existe
        if not Path(code_path).exists():
            print(f"Erro: Arquivo UEFI CODE não encontrado: {code_path}", file=sys.stderr)
            print(f"Verifique o caminho em {GLOBAL_CONF} [firmware_paths]")
            sys.exit(1)
            
        qemu_cmd.extend([
            "-drive", f"if=pflash,format=raw,readonly=on,file={code_path}",
            "-drive", f"if=pflash,format=raw,file={vm_vars_path}"
        ])

    # 6. Disco Principal
    qemu_cmd.extend([
        "-drive", f"file={image_path},if=virtio,format={image_format},media=disk"
    ])

    # 7. Rede (COM MAC)
    bridge = config.get("network", "bridge")
    mac = config.get("network", "mac", fallback=None) 

    net_device_str = f"bridge,id=net0,br={bridge}"
    virtio_net_str = f"virtio-net-pci,netdev=net0"
    
    if mac: 
        virtio_net_str += f",mac={mac}"
        
    qemu_cmd.extend(["-device", virtio_net_str, "-netdev", net_device_str])

    # 8. ISOs (se fornecidos)
    if iso_list:
        print(f"Anexando {len(iso_list)} ISO(s)...")
        qemu_cmd.extend(["-device", "ahci,id=ahci0"])
        
        for i, iso_path_str in enumerate(iso_list):
            iso_path = Path(iso_path_str)
            if not iso_path.exists():
                print(f"Erro: Arquivo ISO não encontrado: {iso_path}", file=sys.stderr)
                sys.exit(1)
            
            drive_id = f"cdrom_sata_{i}"
            qemu_cmd.extend([
                "-drive", f"file={iso_path_str},id={drive_id},if=none,media=cdrom,readonly=on",
                "-device", f"ide-cd,bus=ahci0.{i},drive={drive_id}"
            ])
    
    # 9. Flags Extras (Apenas para modo headless)
    if not graphical_mode and config.has_option("options", "extra_flags"):
        print("Aviso: [options]extra_flags são ignoradas no modo headless (start). Use --vga para aplicá-las.")

    return qemu_cmd

def handle_start(args):
    """Inicia uma VM em modo headless (padrão) ou gráfico (com --vga)."""
    vm_name = args.vm_name
    config = get_vm_config(vm_name) 
    pid_file, sock_file = get_vm_paths(vm_name)

    if not args.vga and is_vm_running(pid_file):
        print(f"Erro: VM '{vm_name}' já parece estar rodando (headless).", file=sys.stderr)
        sys.exit(1)
    elif args.vga and is_vm_running(pid_file):
        print(f"Aviso: VM '{vm_name}' já está rodando em headless.", file=sys.stderr)
        print("Iniciar em modo gráfico pode causar conflitos. Continuando em 5s...")
        time.sleep(5)

    try:
        qemu_cmd = _build_qemu_command(
            vm_name, 
            config, 
            iso_list=args.iso, 
            graphical_mode=args.vga
        )
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        print(f"Erro: Opção de configuração ausente ou inválida: {e}", file=sys.stderr)
        sys.exit(1)
    
    # print(f"Comando: {' '.join(qemu_cmd)}") # Descomente para debug
    
    try:
        if args.vga:
            print(f"Iniciando VM '{vm_name}' em modo GRÁFICO (foreground)...")
            subprocess.run(qemu_cmd, check=True)
            print(f"Sessão gráfica de '{vm_name}' finalizada.")
        else:
            print(f"Iniciando VM '{vm_name}' (headless)...")
            subprocess.run(qemu_cmd, check=True)
            
            time.sleep(1) 
            if is_vm_running(pid_file):
                print(f"VM '{vm_name}' iniciada com sucesso.")
                print(f"  PID: {pid_file.read_text().strip()}")
                print(f"  Monitor: {sock_file}")
            else:
                print(f"Erro: A VM falhou ao iniciar. Verifique os logs do QEMU.", file=sys.stderr)

    except subprocess.CalledProcessError as e:
        print(f"\nErro ao executar o QEMU.", file=sys.stderr)
    except FileNotFoundError:
        print(f"Erro: Comando '{QEMU_BIN}' não encontrado.", file=sys.stderr)
        sys.exit(1)


def handle_create(args):
    """Cria o .conf, cria o disco, e inicia o instalador gráfico."""
    vm_name = args.vm_name
    conf_file = VMS_DIR / f"{vm_name}.conf"
    
    if conf_file.exists():
        print(f"Erro: VM '{vm_name}' já existe em {conf_file}", file=sys.stderr)
        print("Use 'start' para iniciá-la ou apague o arquivo .conf para recriá-la.")
        sys.exit(1)
    
    pid_file, _ = get_vm_paths(vm_name)
    if is_vm_running(pid_file):
        print(f"Erro: VM '{vm_name}' parece estar rodando (PID existe). Limpe manualmente.", file=sys.stderr)
        sys.exit(1)

    # 1. Carregar defaults do global.conf
    g_config = configparser.ConfigParser()
    # (A verificação de existência do global.conf agora está no main())
    g_config.read(GLOBAL_CONF)
    
    os_profile_section = f"install_defaults_{args.os_type}"
    if not g_config.has_section(os_profile_section):
        print(f"Aviso: Perfil de SO '{os_profile_section}' não encontrado. Usando 'generic'.", file=sys.stderr)
        os_profile_section = "install_defaults_generic"
        if not g_config.has_section(os_profile_section):
             print(f"Erro: Perfil 'install_defaults_generic' também não foi encontrado em {GLOBAL_CONF}", file=sys.stderr)
             sys.exit(1)
    
    # 2. Definir valores (padrão do perfil, depois sobrescritos por args)
    smp = args.smp or g_config.get(os_profile_section, "smp", fallback="2")
    memory = args.mem or g_config.get(os_profile_section, "memory", fallback="2G")
    disk_size = args.size or g_config.get(os_profile_section, "disk_size", fallback="16G")
    bridge = args.bridge or g_config.get("network", "bridge", fallback="br0")
    pool_name = args.pool or "default"
    
    # 3. Gerar MAC e UUID
    mac = _generate_mac()
    vm_uuid = str(uuid.uuid4()) 

    # 4. Preparar para criar o arquivo de config
    new_config = configparser.ConfigParser()
    new_config["hardware"] = {
        "smp": smp,
        "memory": memory,
        "firmware": g_config.get("hardware", "firmware", fallback="uefi"),
        "chipset": g_config.get("hardware", "chipset", fallback="q35"),
        "uuid": vm_uuid,
        "os_type": args.os_type # <-- SALVA O OS_TYPE
    }
    new_config["network"] = {
        "bridge": bridge,
        "mac": mac
    }
    
    image_file_name = f"{vm_name}.qcow2"
    disk_config = {
        "image_file": image_file_name,
        "image_format": g_config.get("disks", "image_format", fallback="qcow2")
    }
    if args.pool and args.pool != "default": 
        disk_config["image_pool"] = args.pool
        
    new_config["disks"] = disk_config
    
    print(f"--- Criando nova VM: {vm_name} ---")
    print(f"  Tipo:     {args.os_type}")
    print(f"  Memória:  {memory}, SMP: {smp}")
    print(f"  Pool:     {pool_name}")
    print(f"  Disco:    {image_file_name} ({disk_size})")
    print(f"  Bridge:   {bridge}")
    print(f"  MAC:      {mac}")
    print(f"  UUID:     {vm_uuid}") 
    
    # 5. Escrever o arquivo .conf
    try:
        with open(conf_file, 'w') as f:
            f.write(f"; VM '{vm_name}' gerada por vm_manager.py\n")
            new_config.write(f)
        print(f"Arquivo de configuração salvo: {conf_file}")
    except Exception as e:
        print(f"Erro ao salvar arquivo de configuração: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 6. Criar o disco
    try:
        try:
            pool_path_str = g_config.get("pools", pool_name)
        except (configparser.NoSectionError, configparser.NoOptionError):
            print(f"Erro: Pool '{pool_name}' não definido em {GLOBAL_CONF} [pools]", file=sys.stderr)
            raise
            
        image_format = new_config.get("disks", "image_format")
        
        # Garante que o diretório do pool exista
        pool_path = Path(pool_path_str)
        pool_path.mkdir(parents=True, exist_ok=True) # parents=True para caminhos aninhados
        
        image_path = pool_path / image_file_name
        
        print(f"Criando disco em: {image_path} (Tamanho: {disk_size})...")
        subprocess.run(["qemu-img", "create", "-f", image_format, str(image_path), disk_size], check=True, capture_output=True)
    except Exception as e:
        print(f"Falha ao criar imagem de disco: {e}", file=sys.stderr)
        conf_file.unlink()
        sys.exit(1)

    # 7. Iniciar o instalador (em primeiro plano)
    print("\nIniciando instalador em modo gráfico...")
    print("Selecione o dispositivo de boot no menu UEFI/BIOS.")
    print("Feche a janela do QEMU quando a instalação terminar.")
    
    try:
        merged_config = get_vm_config(vm_name)
        qemu_cmd = _build_qemu_command(vm_name, merged_config, iso_list=args.iso, graphical_mode=True)
    except Exception as e:
        print(f"Erro ao construir comando QEMU: {e}", file=sys.stderr)
        sys.exit(1)

    # print(f"Comando: {' '.join(qemu_cmd)}") # Debug
    try:
        subprocess.run(qemu_cmd, check=True)
    except Exception as e:
        print(f"\nO processo QEMU falhou. {e}", file=sys.stderr)

    print(f"Instalação de '{vm_name}' finalizada.")


def send_monitor_command(sock_file: Path, command: str) -> bool:
    """Envia um comando para o soquete do monitor QEMU."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(str(sock_file))
            s.sendall(command.encode('utf-8'))
            return True
    except (FileNotFoundError, ConnectionRefusedError) as e:
        print(f"Não foi possível conectar ao monitor da VM: {e}", file=sys.stderr)
        return False

def handle_stop(args):
    """Envia um comando de desligamento (powerdown ou quit) para a VM."""
    vm_name = args.vm_name
    pid_file, sock_file = get_vm_paths(vm_name)

    if not is_vm_running(pid_file):
        print(f"Erro: VM '{vm_name}' não está rodando.", file=sys.stderr)
        if pid_file.exists(): pid_file.unlink()
        if sock_file.exists(): sock_file.unlink()
        sys.exit(1)

    if args.force:
        print(f"Forçando 'quit' (desligamento imediato) para '{vm_name}'...")
        if not send_monitor_command(sock_file, "quit\n"):
            sys.exit(1)
    else:
        print(f"Tentando desligamento ACPI (powerdown) para '{vm_name}'...")
        if not send_monitor_command(sock_file, "system_powerdown\n"):
            sys.exit(1)
        
        for i in range(15):
            if not is_vm_running(pid_file):
                print("VM desligada com sucesso (powerdown).")
                if sock_file.exists(): sock_file.unlink() 
                return
            time.sleep(1)

        print("VM não respondeu ao powerdown. Forçando 'quit'...")
        if not send_monitor_command(sock_file, "quit\n"):
            sys.exit(1)

    print("Aguardando processo QEMU finalizar...")
    for _ in range(5): 
        if not is_vm_running(pid_file):
            print("VM finalizada.")
            if sock_file.exists(): sock_file.unlink()
            return
        time.sleep(1)

    print(f"Erro: A VM ainda está rodando. Verifique o PID: {pid_file.read_text().strip()}", file=sys.stderr)

def handle_remove(args):
    """Para, remove o .conf, o disco .qcow2, e os arquivos _VARS.fd de uma VM."""
    vm_name = args.vm_name
    conf_file = VMS_DIR / f"{vm_name}.conf"
    uefi_vars_file = VMS_DIR / f"{vm_name}_VARS.fd"
    pid_file, _ = get_vm_paths(vm_name)

    # 1. Tentar ler o config. Se não existir, não há o que remover.
    try:
        config = get_vm_config(vm_name)
    except SystemExit:
        sys.exit(1) 

    # 2. Parar a VM se estiver rodando
    if is_vm_running(pid_file):
        print(f"VM '{vm_name}' está rodando. Forçando o desligamento...")
        stop_args = argparse.Namespace(vm_name=vm_name, force=True)
        handle_stop(stop_args)
        print("---")

    # 3. Encontrar todos os arquivos
    try:
        disk_file_str, _ = resolve_image_path(config)
        disk_file = Path(disk_file_str)
    except Exception as e:
        print(f"Erro ao resolver caminho do disco: {e}", file=sys.stderr)
        print("Não é possível continuar sem o caminho do disco. Remoção falhou.")
        sys.exit(1)

    # 4. Confirmar com o usuário
    if not args.force:
        print(f"ATENÇÃO: Você está prestes a remover permanentemente '{vm_name}'.")
        print("Os seguintes arquivos serão excluídos:")
        
        files_to_delete = [conf_file, disk_file, uefi_vars_file]
        found_files = False
        for f in files_to_delete:
            if f.exists():
                print(f"  - {f}")
                found_files = True
        
        if not found_files:
            print("Nenhum arquivo associado foi encontrado. Limpeza desnecessária.")
            sys.exit(0)

        try:
            confirm = input("Digite 'yes' para confirmar a remoção: ")
        except EOFError:
            print("\nCancelado.")
            sys.exit(1)
            
        if confirm != "yes":
            print("Remoção cancelada.")
            sys.exit(0)
    
    # 5. Excluir os arquivos
    print(f"Removendo VM '{vm_name}'...")
    try:
        if conf_file.exists():
            conf_file.unlink()
            print(f"Removido: {conf_file}")
        
        if disk_file.exists():
            disk_file.unlink()
            print(f"Removido: {disk_file}")
            
        if uefi_vars_file.exists():
            uefi_vars_file.unlink()
            print(f"Removido: {uefi_vars_file}")
            
    except Exception as e:
        print(f"Erro durante a exclusão de arquivos: {e}", file=sys.stderr)
        print("Alguns arquivos podem ter permanecido.")
        sys.exit(1)
        
    print(f"VM '{vm_name}' removida com sucesso.")


def main():
    # Garante que os diretórios e o config padrão existam
    VMS_DIR.mkdir(exist_ok=True)
    
    if not GLOBAL_CONF.exists():
        print(f"Aviso: Arquivo de configuração global '{GLOBAL_CONF}' não encontrado.")
        print("Criando um arquivo padrão...")
        try:
            with open(GLOBAL_CONF, 'w') as f:
                f.write(DEFAULT_GLOBAL_CONF)
            print(f"Arquivo '{GLOBAL_CONF}' criado com sucesso.")
            
            print("\n!!! ATENÇÃO: Edite o global.conf para ajustar os caminhos (bridge, firmware_paths) !!!\n")
            
        except Exception as e:
            print(f"Erro ao criar '{GLOBAL_CONF}': {e}", file=sys.stderr)
            sys.exit(1)
    
    parser = argparse.ArgumentParser(
        description="Gerenciador simples de VMs QEMU",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Comando 'list'
    list_parser = subparsers.add_parser("list", help="Lista todas as VMs ou detalhes de uma específica.")
    list_parser.add_argument("vm_name", nargs="?", default=None, help="Nome opcional da VM para listar detalhes.")
    list_parser.set_defaults(func=handle_list)

    # Comando 'status'
    status_parser = subparsers.add_parser("status", help="Verifica o status de uma VM.")
    status_parser.add_argument("vm_name", help="O nome da VM (ex: windows10)")
    status_parser.set_defaults(func=handle_status)

    # Comando 'start'
    start_parser = subparsers.add_parser("start", help="Inicia uma VM existente (headless ou gráfica).")
    start_parser.add_argument("vm_name", help="O nome da VM (ex: windows10)")
    start_parser.add_argument(
        "--iso", 
        action="append", 
        help="Caminho opcional para um ISO (modo de manutenção)."
    )
    start_parser.add_argument(
        "--vga", 
        action="store_true", 
        help="Inicia em modo gráfico (primeiro plano) para manutenção."
    )
    start_parser.set_defaults(func=handle_start)

    # Comando 'create'
    create_parser = subparsers.add_parser("create", help="Cria e inicia o instalador de uma nova VM.")
    create_parser.add_argument("vm_name", help="O nome da nova VM a ser criada (ex: windows11)")
    create_parser.add_argument(
        "--iso", 
        required=True, 
        action="append", 
        help="Caminho para um ISO. Use múltiplas vezes (ex: --iso win.iso --iso drivers.iso)"
    )
    create_parser.add_argument("--os-type", choices=['windows', 'linux', 'generic'], default='generic', help="Tipo de SO para aplicar padrões (default: generic)")
    create_parser.add_argument("--smp", help="Sobrescreve o SMP padrão (ex: 8)")
    create_parser.add_argument("--mem", help="Sobrescreve a Memória padrão (ex: 8G)")
    create_parser.add_argument("--size", help="Sobrescreve o Tamanho de disco padrão (ex: 100G)")
    create_parser.add_argument("--bridge", help="Sobrescreve a Bridge padrão (ex: br_tap114)")
    create_parser.add_argument("--pool", help="Nome do pool de armazenamento a ser usado (default: 'default')")
    create_parser.set_defaults(func=handle_create)

    # Comando 'stop'
    stop_parser = subparsers.add_parser("stop", help="Desliga uma VM (via ACPI powerdown).")
    stop_parser.add_argument("vm_name", help="O nome da VM (ex: windows10)")
    stop_parser.add_argument("--force", action="store_true", help="Força o desligamento (hard 'quit') sem tentar powerdown.")
    stop_parser.set_defaults(func=handle_stop)

    # Comando 'remove'
    remove_parser = subparsers.add_parser("remove", help="Remove uma VM (disco, config, e vars).")
    remove_parser.add_argument("vm_name", help="O nome da VM a ser removida.")
    remove_parser.add_argument("--force", action="store_true", help="Pula a confirmação de remoção.")
    remove_parser.set_defaults(func=handle_remove)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()