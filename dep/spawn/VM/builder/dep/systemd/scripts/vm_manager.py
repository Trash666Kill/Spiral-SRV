#!/usr/bin/env python3

import subprocess
import argparse
import sys
import os
import shutil
import re  # <-- ADICIONADO para validação

# --- ANSI Color Codes ---
GREEN = '\033[32m'
RED = '\033[31m'
YELLOW = '\033[33m'
RESET = '\033[0m'
CYAN = '\033[36m'

# =============================================================================
# --- Global Constants (Padrões Editáveis) ---
# =============================================================================

# --- Caminhos de Binários e Helper ---
QEMU_BINARY = 'qemu-system-x86_64'
QEMU_IMG_BINARY = 'qemu-img'
QEMU_BRIDGE_HELPER = '/usr/lib/qemu/qemu-bridge-helper'

# --- Caminhos de Diretório Padrão ---
DEFAULT_IMG_DIR = "/var/lib/libvirt/images"
DEFAULT_NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"
DEFAULT_STATE_DIR = "/var/run/qemu_vm_manager" # <-- ADICIONADO para PIDs

# --- Padrões de OVMF (UEFI) ---
OVMF_CODE_PATH = '/usr/share/OVMF/OVMF_CODE_4M.fd'
NVRAM_TEMPLATES = [
    '/usr/share/OVMF/OVMF_VARS_4M.fd',
    '/usr/share/OVMF/OVMF_VARS.fd'
]

# --- Padrões de Recursos da VM ---
DEFAULT_SMP = '2'
DEFAULT_MEM = '2G'
DEFAULT_BRIDGE = 'br_tap112'

# =============================================================================


def run_command(cmd_list):
    """Helper function to run external commands and return the exit code."""
    try:
        print(f"{CYAN}*{RESET} EXECUTING: {' '.join(cmd_list)}")
        process = subprocess.run(cmd_list)
        return process.returncode
    except FileNotFoundError:
        print(f"\n{RED}*{RESET} ERROR: Command '{cmd_list[0]}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n{RED}*{RESET} ERROR: Failed to run command: {e}", file=sys.stderr)
        sys.exit(1)

def print_custom_help():
    """Prints the custom help message when no command is given."""
    print(f"\n{GREEN}*{RESET} {CYAN}vm_manager.py: QEMU VM Manager{RESET}")
    print(f"\n{YELLOW}ATTENTION: You must specify an operation mode: 'new', 'run', 'list', 'stop', or 'remove'.{RESET}\n")
    
    print(f"  {GREEN}To create a new VM:{RESET}")
    print(f"    Use the {CYAN}new{RESET} command (runs in foreground for installation):")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py new MyVM --size 20G --iso /path/to/install.iso\n")
    
    print(f"  {GREEN}To run an existing VM:{RESET}")
    print(f"    Use the {CYAN}run{RESET} command (runs in background):")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py run MyVM\n")
    
    print(f"  {GREEN}To stop a running VM:{RESET}")
    print(f"    Use the {CYAN}stop{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py stop MyVM\n")

    print(f"  {GREEN}To list available VM disks and status:{RESET}")
    print(f"    Use the {CYAN}list{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py list\n")

    print(f"  {GREEN}To remove a VM:{RESET}")
    print(f"    Use the {CYAN}remove{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py remove MyVM\n")

    print(f"For full options on a command, run:\n    {YELLOW}./vm_manager.py <command> --help{RESET}\n")

def find_nvram_template():
    """Finds the first available *default* OVMF VARS template."""
    for path in NVRAM_TEMPLATES:
        if os.path.exists(path):
            return path
    return None

def create_nvram_file(guest_name, nvram_dest_path):
    """Copies the OVMF template to the destination NVRAM path."""
    print(f"{GREEN}*{RESET} INFO: Preparing NVRAM file at {CYAN}{nvram_dest_path}{RESET}...")
    
    nvram_template_src = find_nvram_template()

    if not nvram_template_src:
        print(f"{RED}*{RESET} ERROR: Could not find a suitable NVRAM template (tried {NVRAM_TEMPLATES})", file=sys.stderr)
        return False
        
    if not os.path.exists(nvram_template_src):
         print(f"{RED}*{RESET} ERROR: NVRAM template file not found: {YELLOW}{nvram_template_src}{RESET}", file=sys.stderr)
         return False

    try:
        print(f"{CYAN}*{RESET} EXECUTING: copy {nvram_template_src} to {nvram_dest_path}")
        shutil.copyfile(nvram_template_src, nvram_dest_path)
        return True
    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Failed to copy NVRAM file: {e}", file=sys.stderr)
        return False

def main():

    # =====================================================================
    # PASSO 1: Verificação de Permissão (Root)
    # =====================================================================
    if os.geteuid() != 0:
        print(f"{RED}*{RESET} ERROR: Este script precisa ser executado como root (ou com sudo).", file=sys.stderr)
        print(f"{YELLOW}*{RESET} INFO: Necessário para acessar {CYAN}{DEFAULT_IMG_DIR}{RESET}, {CYAN}{DEFAULT_STATE_DIR}{RESET} e usar o {CYAN}{QEMU_BRIDGE_HELPER}{RESET}.")
        sys.exit(1)
            
    # --- Parser Principal ---
    parser = argparse.ArgumentParser(
        description="Script to create, run, list, stop, or remove QEMU VMs.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Operation mode', metavar='COMMAND')

    # --- Sub-comando 'new' ---
    new_parser = subparsers.add_parser(
        'new', 
        help='Create a new VM (runs in foreground)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # ... (argumentos do 'new' permanecem os mesmos) ...
    new_key_args = new_parser.add_argument_group('Required Arguments')
    new_key_args.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name for the new guest (e.g., 'SpiralVM').")
    new_key_args.add_argument('--iso', metavar='<path>', type=str, required=True, help="Path to the ISO image (Mandatory for new).")
    new_key_args.add_argument('--size', metavar='<size>', type=str, required=True, help="Size for new disk (e.g., 20G) (Mandatory for new).")
    new_opt_args = new_parser.add_argument_group('Optional Arguments')
    new_opt_args.add_argument('--disk', metavar='<path>', type=str, help=f"Full path for new .qcow2. (Defaults to {DEFAULT_IMG_DIR}/<guest_name>.qcow2)")
    new_res_args = new_parser.add_argument_group('Resource Overrides')
    new_res_args.add_argument('--smp', metavar='<cores>', type=str, default=DEFAULT_SMP, help=f"Number of CPU cores. Default: {DEFAULT_SMP}")
    new_res_args.add_argument('--mem', metavar='<size>', type=str, default=DEFAULT_MEM, help=f"Amount of memory. Default: {DEFAULT_MEM}")
    new_res_args.add_argument('--bridge', metavar='<bridge_if>', type=str, default=DEFAULT_BRIDGE, help=f"Network bridge interface. Default: {DEFAULT_BRIDGE}")


    # --- Sub-comando 'run' ---
    run_parser = subparsers.add_parser(
        'run', 
        help='Run an existing VM (runs in background)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # ... (argumentos do 'run' permanecem os mesmos) ...
    run_key_args = run_parser.add_argument_group('Required Arguments')
    run_key_args.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name of the guest to run (e.g., 'SpiralVM').")
    run_opt_args = run_parser.add_argument_group('Optional Arguments')
    run_opt_args.add_argument('--disk', metavar='<path>', type=str, help=f"Path to the .qcow2 disk. (Optional, defaults to {DEFAULT_IMG_DIR}/<guest_name>.qcow2)")
    run_opt_args.add_argument('--iso', metavar='<path>', type=str, help="Path to an ISO image for live boot or repair.")
    run_res_args = run_parser.add_argument_group('Resource Overrides')
    run_res_args.add_argument('--smp', metavar='<cores>', type=str, default=DEFAULT_SMP, help=f"Number of CPU cores. Default: {DEFAULT_SMP}")
    run_res_args.add_argument('--mem', metavar='<size>', type=str, default=DEFAULT_MEM, help=f"Amount of memory. Default: {DEFAULT_MEM}")
    run_res_args.add_argument('--bridge', metavar='<bridge_if>', type=str, default=DEFAULT_BRIDGE, help=f"Network bridge interface. Default: {DEFAULT_BRIDGE}")


    # --- Sub-comando 'remove' ---
    remove_parser = subparsers.add_parser(
        'remove', 
        help='Remove a VM (disk and NVRAM)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # ... (argumentos do 'remove' permanecem os mesmos) ...
    remove_key_args = remove_parser.add_argument_group('Required Arguments')
    remove_key_args.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name of the guest to remove (e.g., 'SpiralVM').")
    remove_opt_args = remove_parser.add_argument_group('Optional Arguments')
    remove_opt_args.add_argument('--disk', metavar='<path>', type=str, help=f"Path to the .qcow2 disk. (Optional, defaults to {DEFAULT_IMG_DIR}/<guest_name>.qcow2)")

    
    # --- Sub-comando 'stop' (NOVO) ---
    stop_parser = subparsers.add_parser(
        'stop', 
        help='Stop a running VM (sends SIGTERM)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    stop_parser.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name of the guest to stop.")


    # --- Sub-comando 'list' ---
    list_parser = subparsers.add_parser(
        'list', 
        help='List available VM disks and their status',
        description=f"Scans {DEFAULT_IMG_DIR} for .qcow2 files and checks status in {DEFAULT_STATE_DIR}."
    )

    # --- Lógica de Validação ---
    
    if len(sys.argv) == 1:
        print_custom_help()
        sys.exit(0)

    args = parser.parse_args()

    # =====================================================================
    # PASSO 2: Verificação de Segurança (Argument Injection)
    # =====================================================================
    # Valida o guest_name para todos os comandos que o utilizam
    if args.command in ('new', 'run', 'remove', 'stop'):
        # Regex: Começa com letra/número, seguido por letras/números/hífen/underscore
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', args.guest_name):
            print(f"{RED}*{RESET} ERROR: 'guest_name' inválido ({YELLOW}{args.guest_name}{RESET}).", file=sys.stderr)
            print(f"{YELLOW}*{RESET} INFO: Use apenas letras, números, hífen (-) e underscore (_).")
            sys.exit(1)


    # --- Lógica do 'list' (ATUALIZADA) ---
    if args.command == 'list':
        print(f"{GREEN}*{RESET} INFO: Verificando VMs em {CYAN}{DEFAULT_IMG_DIR}{RESET}...")
        if not os.path.isdir(DEFAULT_IMG_DIR):
            print(f"{RED}*{RESET} ERROR: Diretório padrão não encontrado: {YELLOW}{DEFAULT_IMG_DIR}{RESET}", file=sys.stderr)
            sys.exit(1)
        
        qcow2_files = sorted([f for f in os.listdir(DEFAULT_IMG_DIR) if f.endswith('.qcow2')])
        
        if not qcow2_files:
            print(f"{YELLOW}*{RESET} ATTENTION: Nenhum disco .qcow2 encontrado.", file=sys.stderr)
            sys.exit(0)
            
        print(f"{GREEN}*{RESET} INFO: Encontrado(s) {len(qcow2_files)} disco(s) de VM:")
        
        for f in qcow2_files:
            guest_name = f.replace('.qcow2', '')
            pid_file_path = f"{DEFAULT_STATE_DIR}/{guest_name}.pid"
            
            status = f"{RED}STOPPED{RESET}" # Assume "parado" por padrão
            pid_info = ""

            if os.path.exists(pid_file_path):
                try:
                    with open(pid_file_path, 'r') as pf:
                        pid = int(pf.read().strip())
                    
                    os.kill(pid, 0) # Verifica se o processo realmente existe
                    
                    status = f"{GREEN}RUNNING{RESET}"
                    pid_info = f"(PID: {pid})"

                except (OSError, ValueError, TypeError):
                    status = f"{YELLOW}STALE_PID{RESET}" # Um estado "fantasma"
                    pid_info = f"(Arquivo PID obsoleto encontrado)"

            # Formata a saída para alinhamento
            print(f"  - {CYAN}{guest_name:<25}{RESET} [{status:<18}] {pid_info}")
            
        sys.exit(0)

    # --- Lógica do 'remove' ---
    if args.command == 'remove':
        print(f"{RED}*{RESET} INFO: Attempting to remove VM: {YELLOW}{args.guest_name}{RESET}")
        
        # 1. Encontrar alvos
        if args.disk:
            disk_path = args.disk
        else:
            disk_path = f"{DEFAULT_IMG_DIR}/{args.guest_name}.qcow2"
            
        nvram_path = f"{DEFAULT_NVRAM_DIR}/{args.guest_name}_VARS.fd"
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid" # <-- ADICIONADO
        
        # Verifica se a VM está em execução
        if os.path.exists(pid_file_path):
             try:
                with open(pid_file_path, 'r') as f: pid = int(f.read().strip())
                os.kill(pid, 0) # Verifica o processo
                print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' appears to be running (PID: {pid}).", file=sys.stderr)
                print(f"{YELLOW}*{RESET} INFO: Por favor, pare a VM com o comando 'stop' antes de remover.")
                sys.exit(1)
             except (OSError, ValueError, TypeError):
                pass # PID obsoleto, seguro para remover

        files_to_delete = []
        if os.path.exists(disk_path): files_to_delete.append(disk_path)
        if os.path.exists(nvram_path): files_to_delete.append(nvram_path)
        if os.path.exists(pid_file_path): files_to_delete.append(pid_file_path) # Limpa PID obsoleto
            
        # 2. Confirmar
        if not files_to_delete:
            print(f"{YELLOW}*{RESET} ATTENTION: No files found for guest '{args.guest_name}'. Nothing to do.", file=sys.stderr)
            sys.exit(0)
            
        print(f"{YELLOW}*{RESET} ATTENTION: The following files will be {RED}PERMANENTLY DELETED{YELLOW}:{RESET}")
        for f in files_to_delete:
            print(f"  - {CYAN}{f}{RESET}")
            
        try:
            response = input(f"    {YELLOW}Are you sure you want to continue? [y/N]: {RESET}").strip().lower()
        except EOFError:
            response = 'n'
            
        if response not in ('y', 'yes'):
            print(f"{RED}*{RESET} ERROR: Operation aborted by user.", file=sys.stderr)
            sys.exit(1)
            
        # 3. Excluir
        print(f"{GREEN}*{RESET} INFO: Proceeding with deletion...")
        success = True
        for f in files_to_delete:
            print(f"{YELLOW}*{RESET} ATTENTION: Deleting {CYAN}{f}{RESET}...")
            try:
                os.remove(f)
            except OSError as e:
                print(f"{RED}*{RESET} ERROR: Failed to delete file {f}: {e}", file=sys.stderr)
                success = False
        
        if success:
            print(f"{GREEN}*{RESET} INFO: VM '{args.guest_name}' files removed successfully.")
        else:
            print(f"{RED}*{RESET} ERROR: One or more files could not be removed.", file=sys.stderr)
            sys.exit(1)
        
        sys.exit(0) # Fim do 'remove'


    # --- Lógica do 'stop' (NOVO) ---
    if args.command == 'stop':
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"
        print(f"{GREEN}*{RESET} INFO: Attempting to stop VM: {YELLOW}{args.guest_name}{RESET}")

        if not os.path.exists(pid_file_path):
            print(f"{RED}*{RESET} ERROR: PID file not found. A VM está desligada ou o arquivo foi removido?", file=sys.stderr)
            print(f"{YELLOW}*{RESET} INFO: Path check: {CYAN}{pid_file_path}{RESET}")
            sys.exit(1)

        try:
            with open(pid_file_path, 'r') as f:
                pid = int(f.read().strip())
        except Exception as e:
            print(f"{RED}*{RESET} ERROR: Failed to read PID file: {e}", file=sys.stderr)
            sys.exit(1)

        if pid <= 0:
             print(f"{RED}*{RESET} ERROR: Invalid PID found in file: {pid}", file=sys.stderr)
             sys.exit(1)

        print(f"{CYAN}*{RESET} EXECUTING: Sending SIGTERM (15) to PID {pid}...")
        try:
            # Envia SIGTERM (sinal 15), que é o "shutdown limpo"
            os.kill(pid, 15) 
            print(f"{GREEN}*{RESET} INFO: Shutdown signal sent. A VM deve desligar em breve.")
            
            # Limpa o pidfile
            try: os.remove(pid_file_path)
            except OSError as e: print(f"{YELLOW}*{RESET} ATTENTION: Could not remove PID file: {e}", file=sys.stderr)
            
        except OSError as e:
            print(f"{RED}*{RESET} ERROR: Failed to send signal: {e}", file=sys.stderr)
            # errno 3 = No such process
            if e.errno == 3: 
                print(f"{YELLOW}*{RESET} ATTENTION: Process {pid} not found. Removing stale PID file.", file=sys.stderr)
                try: os.remove(pid_file_path)
                except OSError as e2: print(f"{RED}*{RESET} ERROR: Failed to remove stale PID file: {e2}", file=sys.stderr)
            sys.exit(1)
        
        sys.exit(0) # Fim do 'stop'

    
    # --- Lógica para 'new' e 'run' ---
    
    is_install_boot = False
    
    # Definir caminhos NVRAM (comuns a 'new' e 'run')
    if not os.path.isdir(DEFAULT_NVRAM_DIR):
         print(f"{RED}*{RESET} ERROR: NVRAM directory not found: {YELLOW}{DEFAULT_NVRAM_DIR}{RESET}", file=sys.stderr)
         sys.exit(1)
    nvram_path = f"{DEFAULT_NVRAM_DIR}/{args.guest_name}_VARS.fd"

    # Definir caminho do OVMF CODE
    ovmf_code_path = OVMF_CODE_PATH

    # Validar OVMF_CODE principal
    if not os.path.exists(ovmf_code_path):
        print(f"{RED}*{RESET} ERROR: Base OVMF CODE file not found: {YELLOW}{ovmf_code_path}{RESET}", file=sys.stderr)
        sys.exit(1)


    if args.command == 'new':
        print(f"{GREEN}*{RESET} INFO: 'New VM' mode enabled for {GREEN}{args.guest_name}{RESET}")
        print(f"{YELLOW}*{RESET} ATTENTION: A VM será iniciada em FOREGROUND para instalação.")
        is_install_boot = True
        
        # 1. Definir caminho do disco
        if args.disk:
            disk_path = args.disk
        else:
            disk_path = f"{DEFAULT_IMG_DIR}/{args.guest_name}.qcow2"
            print(f"{YELLOW}*{RESET} ATTENTION: --disk not specified. Defaulting to: {CYAN}{disk_path}{RESET}")
            if not os.path.isdir(DEFAULT_IMG_DIR):
                print(f"{RED}*{RESET} ERROR: Default directory not found: {YELLOW}{DEFAULT_IMG_DIR}{RESET}", file=sys.stderr)
                sys.exit(1)
        
        # 2. Verificar arquivos existentes (com prompt)
        disk_exists = os.path.exists(disk_path)
        nvram_exists = os.path.exists(nvram_path)
        
        if disk_exists or nvram_exists:
            print(f"{YELLOW}*{RESET} ATTENTION: Existing files found for guest '{args.guest_name}':")
            if disk_exists: print(f"    - Disk:   {CYAN}{disk_path}{RESET}")
            if nvram_exists: print(f"    - NVRAM:  {CYAN}{nvram_path}{RESET}")
            
            try:
                response = input(f"    {YELLOW}Do you want to delete them and continue? [y/N]: {RESET}").strip().lower()
            except EOFError: response = 'n'
            
            if response not in ('y', 'yes'):
                print(f"{RED}*{RESET} ERROR: Aborting operation. Files not removed.", file=sys.stderr)
                sys.exit(1)

            if disk_exists:
                print(f"{YELLOW}*{RESET} ATTENTION: Deleting existing disk: {CYAN}{disk_path}{RESET}")
                try: os.remove(disk_path)
                except OSError as e: print(f"{RED}*{RESET} ERROR: Failed to delete disk: {e}", file=sys.stderr); sys.exit(1)
            if nvram_exists:
                print(f"{YELLOW}*{RESET} ATTENTION: Deleting existing NVRAM: {CYAN}{nvram_path}{RESET}")
                try: os.remove(nvram_path)
                except OSError as e: print(f"{RED}*{RESET} ERROR: Failed to delete NVRAM: {e}", file=sys.stderr); sys.exit(1)
        
        # 3. Validar ISO
        if not os.path.exists(args.iso):
            print(f"{RED}*{RESET} ERROR: ISO file not found at: {YELLOW}{args.iso}{RESET}", file=sys.stderr)
            sys.exit(1)

        # 4. Criar Disco
        print(f"{GREEN}*{RESET} INFO: Creating new disk {GREEN}{disk_path}{RESET} with size {GREEN}{args.size}{RESET}...")
        create_cmd = [QEMU_IMG_BINARY, 'create', '-f', 'qcow2', disk_path, args.size]
        if run_command(create_cmd) != 0:
             print(f"{RED}*{RESET} ERROR: qemu-img create failed.", file=sys.stderr)
             sys.exit(1)

        # 5. Criar NVRAM (lógica padrão)
        if not create_nvram_file(args.guest_name, nvram_path):
            print(f"{RED}*{RESET} ERROR: Failed to create NVRAM file.", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == 'run':
        print(f"{GREEN}*{RESET} INFO: 'Run VM' mode enabled for {GREEN}{args.guest_name}{RESET}")
        print(f"{YELLOW}*{RESET} ATTENTION: A VM será iniciada em BACKGROUND.")

        # --- Lógica de Gerenciamento de Estado (ATUALIZADO) ---
        if not os.path.exists(DEFAULT_STATE_DIR):
            try:
                os.makedirs(DEFAULT_STATE_DIR, 0o755)
                print(f"{GREEN}*{RESET} INFO: Created state directory {CYAN}{DEFAULT_STATE_DIR}{RESET}")
            except OSError as e:
                print(f"{RED}*{RESET} ERROR: Failed to create state directory: {e}", file=sys.stderr)
                sys.exit(1)
        
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"

        if os.path.exists(pid_file_path):
            try:
                with open(pid_file_path, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0) # Verifica se o processo existe
                print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' appears to be running (PID: {pid}).", file=sys.stderr)
                print(f"{YELLOW}*{RESET} INFO: Use 'stop' para pará-la.")
                sys.exit(1)
            except (OSError, ValueError, TypeError):
                print(f"{YELLOW}*{RESET} ATTENTION: Removing stale PID file {CYAN}{pid_file_path}{RESET}")
                try: os.remove(pid_file_path)
                except OSError as e: print(f"{RED}*{RESET} ERROR: Failed to remove stale PID file: {e}", file=sys.stderr)

        # --- Fim da lógica de estado ---


        # 1. Definir caminho do disco (com padrão)
        if args.disk:
            disk_path = args.disk
        else:
            disk_path = f"{DEFAULT_IMG_DIR}/{args.guest_name}.qcow2"
            print(f"{YELLOW}*{RESET} ATTENTION: --disk not specified. Assuming: {CYAN}{disk_path}{RESET}")

        # 2. Validar Disco
        if not os.path.exists(disk_path):
            print(f"{RED}*{RESET} ERROR: qcow2 disk file not found at: {YELLOW}{disk_path}{RESET}", file=sys.stderr)
            sys.exit(1)
            
        # 3. Validar NVRAM (e criar se necessário)
        if not os.path.exists(nvram_path):
            print(f"{YELLOW}*{RESET} ATTENTION: NVRAM file not found. Attempting to create...", file=sys.stderr)
            if not create_nvram_file(args.guest_name, nvram_path):
                print(f"{RED}*{RESET} ERROR: Failed to create missing NVRAM file.", file=sys.stderr)
                sys.exit(1)

        # 4. Lógica da ISO
        if args.iso:
            if not os.path.exists(args.iso):
                print(f"{RED}*{RESET} ERROR: ISO file not found at: {YELLOW}{args.iso}{RESET}", file=sys.stderr)
                sys.exit(1)
            print(f"{GREEN}*{RESET} INFO: ISO provided. Setting as primary boot device.")
            is_install_boot = True
        else:
            is_install_boot = False

    # --- Construção e Execução do Comando QEMU (para 'new' e 'run') ---

    qemu_command = [
        QEMU_BINARY,
        '-enable-kvm',
        '-cpu', 'host',
        '-smp', args.smp,
        '-m', args.mem,
        '-drive', f'file={disk_path},if=virtio,format=qcow2', 
        '-device', 'virtio-net-pci,netdev=net0',
        '-netdev', f'tap,id=net0,br={args.bridge},helper={QEMU_BRIDGE_HELPER}',
        '-device', 'virtio-vga',
        '-drive', f'if=pflash,format=raw,readonly=on,file={ovmf_code_path}',
        '-drive', f'if=pflash,format=raw,file={nvram_path}',
    ]

    # --- Adiciona flags de daemonização APENAS para 'run' ---
    if args.command == 'run':
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"
        qemu_command.extend([
            '-daemonize',
            '-pidfile', pid_file_path
        ])

    # --- Lógica de Boot (ISO) ---
    if args.iso:
        qemu_command.extend([
            '-drive', f'file={args.iso},media=cdrom'
        ])
    
    if is_install_boot:
        print(f"{GREEN}*{RESET} INFO: Setting boot order to CD-ROM (d).")
        qemu_command.extend([
            '-boot', 'order=d'
        ])
    else:
        print(f"{GREEN}*{RESET} INFO: Booting from disk (default order).")

    # --- Execução ---
    print(f"\n{GREEN}*{RESET} INFO: Final command to be executed:")
    print(' '.join(qemu_command))
    print("-" * 70)

    try:
        # Executa o QEMU
        return_code = run_command(qemu_command)
        if return_code != 0:
             print(f"\n{RED}*{RESET} ERROR: QEMU command failed (exit code {RED}{return_code}{RESET}).", file=sys.stderr)
             sys.exit(return_code)
        
        if args.command == 'run':
            print(f"\n{GREEN}*{RESET} INFO: VM '{args.guest_name}' iniciada em background.")
            
    except KeyboardInterrupt:
        print(f"\n{YELLOW}*{RESET} ATTENTION: VM boot (new) interrupted by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()