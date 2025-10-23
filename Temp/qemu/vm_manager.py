#!/usr/bin/env python3

import argparse
import configparser
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

# --- Constantes de Configuração ---
VMS_DIR = Path("vms")
GLOBAL_CONF = Path("global.conf")
QEMU_BIN = "qemu-system-x86_64"
# ---------------------------------

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
        return False # Arquivo PID corrompido ou desapareceu

    try:
        # Envia um "sinal 0" para o processo.
        # Não faz nada, mas falha se o processo não existir.
        os.kill(pid, 0)
    except ProcessLookupError:
        return False # Processo não existe (PID "velho")
    except PermissionError:
        # Processo existe, mas não somos donos (provavelmente do root)
        # Como o script deve rodar como root, isso indica que ele está rodando.
        return True
    
    return True # Processo existe

def resolve_image_path(config: configparser.ConfigParser) -> (str, str):
    """Resolve o caminho final do image_file usando a lógica de pools."""
    try:
        image_file = config.get("disks", "image_file")
        image_format = config.get("disks", "image_format", fallback="qcow2")
    except configparser.NoOptionError as e:
        print(f"Erro: Configuração obrigatória '{e.option}' não encontrada na seção [disks].", file=sys.stderr)
        sys.exit(1)

    # Se o caminho for absoluto, use-o diretamente.
    if Path(image_file).is_absolute():
        return image_file, image_format

    # Se for relativo, use os pools
    try:
        # Tenta pegar o pool nomeado; se não existir, usa 'default'
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
        # get_vm_config já lida com o erro de arquivo não encontrado
        config = get_vm_config(vm_name)
    except SystemExit:
        return # Erro já foi impresso por get_vm_config

    pid_file, sock_file = get_vm_paths(vm_name)
    running = is_vm_running(pid_file)
    
    print(f"--- Detalhes da VM: {vm_name} ---")
    
    # --- Status de Execução ---
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

    # --- Configuração de Hardware ---
    print(f"\n[Hardware (Configurado)]")
    print(f"  Memória:    {config.get('hardware', 'memory', fallback='N/A')}")
    print(f"  SMP (vCPUs):{config.get('hardware', 'smp', fallback='N/A')}")
    print(f"  CPU (fixo): host")
    print(f"  KVM (fixo): habilitado")

    # --- Configuração de Disco (Resolvido) ---
    print(f"\n[Disks (Resolvido)]")
    try:
        image_path, image_format = resolve_image_path(config)
        print(f"  Imagem:     {image_path}")
        print(f"  Formato:    {image_format}")
    except Exception as e:
        print(f"  Imagem:     (Erro ao resolver: {e})")

    # --- Configuração de Rede (Resolvido) ---
    print(f"\n[Network (Resolvido)]")
    try:
        print(f"  Bridge:     {config.get('network', 'bridge')}")
    except (configparser.NoSectionError, configparser.NoOptionError):
        print("  Bridge:     (Nenhuma bridge definida no global.conf ou vm.conf)")

    # --- Opções Extras ---
    print(f"\n[Options (Configurado)]")
    extra_flags = config.get('options', 'extra_flags', fallback="Nenhum")
    print(f"  Flags Extras: {extra_flags}")

def handle_list(args):
    """Lista todas as VMs ou detalhes de uma VM específica."""
    if args.vm_name:
        # Se um nome foi fornecido, mostre detalhes e saia
        _show_vm_details(args.vm_name)
        return

    # Se nenhum nome foi fornecido, liste todas as VMs (lógica antiga)
    print("VMs definidas:")
    
    vm_files = sorted(list(VMS_DIR.glob("*.conf")))
    if not vm_files:
        print(f"  (Nenhum arquivo .conf encontrado em '{VMS_DIR}/')")
        return

    # Encontra o nome mais longo para formatação
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

def handle_start(args):
    """Inicia uma nova VM em segundo plano."""
    vm_name = args.vm_name
    config = get_vm_config(vm_name)
    pid_file, sock_file = get_vm_paths(vm_name)

    if is_vm_running(pid_file):
        print(f"Erro: VM '{vm_name}' já parece estar rodando.", file=sys.stderr)
        sys.exit(1)

    # 1. Resolver caminhos
    image_path, image_format = resolve_image_path(config)
    if not Path(image_path).exists():
        print(f"Erro: Arquivo de imagem não encontrado: {image_path}", file=sys.stderr)
        print(f"Dica: Crie-o com: qemu-img create -f {image_format} {image_path} 32G", file=sys.stderr)
        sys.exit(1)

    # 2. Montar o comando QEMU base
    try:
        qemu_cmd = [
            QEMU_BIN,
            # --- Início das Flags Hardcoded ---
            "-enable-kvm",
            "-cpu", "host",
            # --- Fim das Flags Hardcoded ---
            "-smp", config.get("hardware", "smp", fallback="2"),
            "-m", config.get("hardware", "memory", fallback="2G"),
            "-boot", "menu=on",
            "-daemonize",
            "-pidfile", str(pid_file),
            "-monitor", f"unix:{sock_file},server,nowait",
        ]

        # 3. Adicionar disco principal (VirtIO)
        qemu_cmd.extend([
            "-drive", f"file={image_path},if=virtio,format={image_format},media=disk"
        ])

        # 4. Adicionar rede (VirtIO Bridge)
        bridge = config.get("network", "bridge")
        qemu_cmd.extend([
            "-device", "virtio-net-pci,netdev=net0",
            "-netdev", f"bridge,id=net0,br={bridge}",
        ])

        # 5. Adicionar CD-ROM (SATA) se --iso foi fornecido
        if args.iso:
            iso_path = Path(args.iso)
            if not iso_path.exists():
                print(f"Erro: Arquivo ISO não encontrado: {iso_path}", file=sys.stderr)
                sys.exit(1)
            
            print(f"Anexando ISO: {iso_path}")
            qemu_cmd.extend([
                "-device", "ahci,id=ahci0",
                "-drive", f"file={iso_path},id=cdrom_sata,if=none,media=cdrom,readonly=on",
                "-device", "ide-cd,bus=ahci0.0,drive=cdrom_sata",
            ])
        
        # 6. Adicionar flags extras (ex: -vga virtio)
        if config.has_option("options", "extra_flags"):
            extra_flags = config.get("options", "extra_flags")
            qemu_cmd.extend(shlex.split(extra_flags))

    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        print(f"Erro: Opção de configuração ausente ou inválida:", file=sys.stderr)
        if hasattr(e, 'section') and hasattr(e, 'option'):
            print(f"  Seção: {e.section}, Opção: {e.option}", file=sys.stderr)
        else:
            print(f"  Detalhe: {e}", file=sys.stderr)
        sys.exit(1)
    
    # 7. Executar o comando
    print(f"Iniciando VM '{vm_name}'...")
    # print(f"Comando: {' '.join(qemu_cmd)}") # Descomente para debug
    
    try:
        subprocess.run(qemu_cmd, check=True)
    except subprocess.CalledProcessError as e:
        print(f"\nErro ao iniciar o QEMU. A configuração pode ser inválida ou permissões estão faltando.", file=sys.stderr)
        print(f"Lembre-se: O script deve ser executado como root.", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print(f"Erro: Comando '{QEMU_BIN}' não encontrado.", file=sys.stderr)
        sys.exit(1)
    
    # 8. Verificar sucesso
    time.sleep(1) # Dá tempo para o -daemonize criar o pidfile
    if is_vm_running(pid_file):
        print(f"VM '{vm_name}' iniciada com sucesso.")
        print(f"  PID: {pid_file.read_text().strip()}")
        print(f"  Monitor: {sock_file}")
    else:
        print(f"Erro: A VM falhou ao iniciar. Verifique os logs do QEMU.", file=sys.stderr)

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
        # Limpa arquivos "velhos" se existirem
        if pid_file.exists(): pid_file.unlink()
        if sock_file.exists(): sock_file.unlink()
        sys.exit(1)

    # Lógica de desligamento: forçado ou graceful
    if args.force:
        # --force: Envia 'quit' imediatamente
        print(f"Forçando 'quit' (desligamento imediato) para '{vm_name}'...")
        if not send_monitor_command(sock_file, "quit\n"):
            sys.exit(1)
    else:
        # Padrão: Tenta 'system_powerdown' primeiro
        print(f"Tentando desligamento ACPI (powerdown) para '{vm_name}'...")
        if not send_monitor_command(sock_file, "system_powerdown\n"):
            sys.exit(1)
        
        # Espera até 15 segundos pelo desligamento
        for i in range(15):
            if not is_vm_running(pid_file):
                print("VM desligada com sucesso (powerdown).")
                if sock_file.exists(): sock_file.unlink() # pid_file é apagado pelo QEMU
                return
            time.sleep(1)

        # Se ainda estiver rodando, força o 'quit'
        print("VM não respondeu ao powerdown. Forçando 'quit'...")
        if not send_monitor_command(sock_file, "quit\n"):
            sys.exit(1)

    # Bloco final de verificação (para 'quit' forçado ou fallback)
    print("Aguardando processo QEMU finalizar...")
    for _ in range(5): # Espera 5 segundos pelo 'quit'
        if not is_vm_running(pid_file):
            print("VM finalizada.")
            if sock_file.exists(): sock_file.unlink()
            return
        time.sleep(1)

    print(f"Erro: A VM ainda está rodando. Verifique o PID: {pid_file.read_text().strip()}", file=sys.stderr)

def main():
    # Cria o diretório de VMs se não existir
    VMS_DIR.mkdir(exist_ok=True)

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
    start_parser = subparsers.add_parser("start", help="Inicia uma VM em segundo plano.")
    start_parser.add_argument("vm_name", help="O nome da VM (ex: windows10)")
    start_parser.add_argument("--iso", help="Caminho opcional para um .iso para anexar como CD-ROM")
    start_parser.set_defaults(func=handle_start)

    # Comando 'stop'
    stop_parser = subparsers.add_parser("stop", help="Desliga uma VM (via ACPI powerdown).")
    stop_parser.add_argument("vm_name", help="O nome da VM (ex: windows10)")
    stop_parser.add_argument("--force", action="store_true", help="Força o desligamento (hard 'quit') sem tentar powerdown.")
    stop_parser.set_defaults(func=handle_stop)

    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()