#!/usr/bin/env python3

import subprocess
import argparse
import sys
import os
import shutil
import re
import configparser
import random         # <<< [NOVO] Para gerar MACs

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
DEFAULT_STATE_DIR = "/var/run/qemu_vm_manager"
DEFAULT_CONF_DIR = "/etc/vm_manager/vms" 

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
# --- Funções Helper ---
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

# <<< [NOVO] Função para gerar MAC aleatório com prefixo QEMU >>>
def generate_random_mac():
    """Generates a random MAC address with QEMU prefix 52:54:00."""
    # Prefixo padrão do QEMU/KVM
    mac = [0x52, 0x54, 0x00,
           random.randint(0x00, 0xFF),
           random.randint(0x00, 0xFF),
           random.randint(0x00, 0xFF)]
    # Formata como string "02x" (hex com 2 dígitos, preenchido com zero)
    return ':'.join(f"{b:02x}" for b in mac)

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
        nvram_dir = os.path.dirname(nvram_dest_path)
        os.makedirs(nvram_dir, 0o755, exist_ok=True) 

        print(f"{CYAN}*{RESET} EXECUTING: copy {nvram_template_src} to {nvram_dest_path}")
        shutil.copyfile(nvram_template_src, nvram_dest_path)
        return True
    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Failed to copy/create NVRAM file: {e}", file=sys.stderr)
        return False

def load_vm_config(guest_name):
    """Loads VM configuration file and returns a dictionary."""
    conf_path = os.path.join(DEFAULT_CONF_DIR, f"{guest_name}.conf")
    if not os.path.exists(conf_path):
        return {}
    
    try:
        parser = configparser.ConfigParser()
        parser.read(conf_path)
        if 'VM' in parser:
            return dict(parser['VM'])
        return {}
    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Failed to parse config file {conf_path}: {e}", file=sys.stderr)
        return {}

def write_vm_config(guest_name, config_data):
    """Writes a dictionary to a VM configuration file."""
    try:
        os.makedirs(DEFAULT_CONF_DIR, 0o755, exist_ok=True)
        conf_path = os.path.join(DEFAULT_CONF_DIR, f"{guest_name}.conf")
        
        parser = configparser.ConfigParser()
        parser['VM'] = config_data
        
        with open(conf_path, 'w') as f:
            parser.write(f)
        
        print(f"{GREEN}*{RESET} INFO: VM configuration written to {CYAN}{conf_path}{RESET}")
        return True
    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Failed to write config file: {e}", file=sys.stderr)
        return False

def print_custom_help():
    """Prints the custom help message when no command is given."""
    print(f"\n{GREEN}*{RESET} {CYAN}vm_manager.py: QEMU VM Manager{RESET}")
    print(f"\n{YELLOW}ATTENTION: You must specify an operation mode: 'new', 'run', 'list', 'stop', 'remove', or 'copy'.{RESET}\n")
    
    print(f"  {GREEN}To create a new VM:{RESET}")
    print(f"    Use the {CYAN}new{RESET} command (runs in foreground for installation):")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py new MyVM --size 20G --iso /path/to/install.iso\n")
    
    print(f"  {GREEN}To run an existing VM:{RESET}")
    print(f"    Use the {CYAN}run{RESET} command (runs in background):")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py run MyVM")
    print(f"    {YELLOW}Example (Override):{RESET} ./vm_manager.py run MyVM --mem 8G --headless\n")

    print(f"  {GREEN}To clone an existing VM:{RESET}")
    print(f"    Use the {CYAN}copy{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py copy MyVM MyVM-Backup\n")
    
    print(f"  {GREEN}To stop a running VM:{RESET}")
    print(f"    Use the {CYAN}stop{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py stop MyVM\n")

    print(f"  {GREEN}To list defined VMs and status:{RESET}")
    print(f"    Use the {CYAN}list{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py list\n")

    print(f"  {GREEN}To remove a VM:{RESET}")
    print(f"    Use the {CYAN}remove{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./vm_manager.py remove MyVM\n")

    print(f"For full options on a command, run:\n    {YELLOW}./vm_manager.py <command> --help{RESET}\n")

# =============================================================================
# --- Main Function ---
# =============================================================================

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
        help='Create and define a new VM (runs in foreground)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
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
    new_res_args.add_argument('--mac', metavar='<addr>', type=str, default=None, help="Specify a custom MAC (default: auto-generate).")


    # --- Sub-comando 'run' ---
    run_parser = subparsers.add_parser(
        'run', 
        help='Run a defined VM (runs in background)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    run_key_args = run_parser.add_argument_group('Required Arguments')
    run_key_args.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name of the defined guest to run (e.g., 'SpiralVM').")
    
    run_opt_args = run_parser.add_argument_group('Optional Overrides')
    run_opt_args.add_argument('--disk', metavar='<path>', type=str, default=None, help=f"Override path to the .qcow2 disk.")
    run_opt_args.add_argument('--iso', metavar='<path>', type=str, default=None, help="Path to an ISO image for live boot or repair.")
    run_opt_args.add_argument('--headless', action='store_true', default=False, help="Override: Run in headless mode (no graphical display).")
    run_res_args = run_parser.add_argument_group('Resource Overrides')
    run_res_args.add_argument('--smp', metavar='<cores>', type=str, default=None, help=f"Override number of CPU cores.")
    run_res_args.add_argument('--mem', metavar='<size>', type=str, default=None, help=f"Override amount of memory.")
    run_res_args.add_argument('--bridge', metavar='<bridge_if>', type=str, default=None, help=f"Override network bridge interface.")
    run_res_args.add_argument('--mac', metavar='<addr>', type=str, default=None, help="Override custom MAC address.")


    # --- Sub-comando 'remove' ---
    remove_parser = subparsers.add_parser(
        'remove', 
        help='Remove a defined VM (config, disk, and NVRAM)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    remove_key_args = remove_parser.add_argument_group('Required Arguments')
    remove_key_args.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name of the defined guest to remove (e.g., 'SpiralVM').")

    
    # --- Sub-comando 'stop' ---
    stop_parser = subparsers.add_parser(
        'stop', 
        help='Stop a running VM (sends SIGTERM)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    stop_parser.add_argument('guest_name', metavar='GUEST_NAME', type=str, help="Name of the guest to stop.")


    # --- Sub-comando 'list' ---
    list_parser = subparsers.add_parser(
        'list', 
        help='List defined VMs and their status', 
        description=f"Scans {DEFAULT_CONF_DIR} for .conf files and checks status in {DEFAULT_STATE_DIR}."
    )
    
    # --- Sub-comando 'copy' ---
    copy_parser = subparsers.add_parser(
        'copy', 
        help='Clone a defined VM to a new name',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    copy_parser.add_argument('source_name', metavar='SOURCE_NAME', type=str, help="Name of the existing defined guest to clone.")
    copy_parser.add_argument('dest_name', metavar='DEST_NAME', type=str, help="Name for the new cloned guest.")


    # --- Lógica de Validação ---
    
    if len(sys.argv) == 1:
        print_custom_help()
        sys.exit(0)

    args = parser.parse_args()

    # =====================================================================
    # PASSO 2: Verificação de Segurança (Argument Injection)
    # =====================================================================
    
    if args.command in ('new', 'run', 'remove', 'stop'):
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', args.guest_name):
            print(f"{RED}*{RESET} ERROR: 'guest_name' inválido ({YELLOW}{args.guest_name}{RESET}).", file=sys.stderr)
            print(f"{YELLOW}*{RESET} INFO: Use apenas letras, números, hífen (-) e underscore (_).")
            sys.exit(1)
    elif args.command == 'copy':
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', args.source_name):
            print(f"{RED}*{RESET} ERROR: 'source_name' inválido ({YELLOW}{args.source_name}{RESET}).", file=sys.stderr)
            sys.exit(1)
        if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]*$', args.dest_name):
            print(f"{RED}*{RESET} ERROR: 'dest_name' inválido ({YELLOW}{args.dest_name}{RESET}).", file=sys.stderr)
            sys.exit(1)

    # =====================================================================
    # --- Lógica dos Comandos ---
    # =====================================================================

    # --- Lógica do 'list' ---
    if args.command == 'list':
        print(f"{GREEN}*{RESET} INFO: Verificando VMs definidas em {CYAN}{DEFAULT_CONF_DIR}{RESET}...")
        if not os.path.isdir(DEFAULT_CONF_DIR):
            print(f"{YELLOW}*{RESET} ATTENTION: Diretório de configuração não encontrado. Nenhuma VM definida.", file=sys.stderr)
            sys.exit(0)
        
        conf_files = sorted([f for f in os.listdir(DEFAULT_CONF_DIR) if f.endswith('.conf')])
        
        if not conf_files:
            print(f"{YELLOW}*{RESET} ATTENTION: Nenhuma VM definida encontrada.", file=sys.stderr)
            sys.exit(0)
            
        print(f"{GREEN}*{RESET} INFO: Encontrada(s) {len(conf_files)} VM(s) definida(s):")
        
        print(f"  {CYAN}{'GUEST NAME':<20}{RESET} {'STATUS':<18} {'DETAILS':<25}")
        print("  " + "-" * 63)

        for f in conf_files:
            guest_name = f.replace('.conf', '')
            pid_file_path = f"{DEFAULT_STATE_DIR}/{guest_name}.pid"
            
            status = f"{RED}STOPPED{RESET}"
            pid_info = ""

            if os.path.exists(pid_file_path):
                try:
                    with open(pid_file_path, 'r') as pf:
                        pid = int(pf.read().strip())
                    os.kill(pid, 0)
                    status = f"{GREEN}RUNNING{RESET}"
                    pid_info = f"(PID: {pid})"
                except (OSError, ValueError, TypeError):
                    status = f"{YELLOW}STALE_PID{RESET}"
                    pid_info = f"(Stale PID)"

            config = load_vm_config(guest_name)
            mem = config.get('mem', 'N/A')
            smp = config.get('smp', 'N/A')
            details = f"{mem} RAM, {smp} Cores"

            print(f"  - {guest_name:<20} [{status:<18}] {details:<25} {pid_info}")
            
        sys.exit(0)

    # --- Lógica do 'remove' ---
    if args.command == 'remove':
        print(f"{RED}*{RESET} INFO: Attempting to remove VM: {YELLOW}{args.guest_name}{RESET}")
        
        config = load_vm_config(args.guest_name)
        if not config:
            print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' não está definida. (Arquivo .conf não encontrado)", file=sys.stderr)
            sys.exit(1)
            
        disk_path = config.get('disk')
        nvram_path = config.get('nvram')
        conf_path = os.path.join(DEFAULT_CONF_DIR, f"{args.guest_name}.conf")
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"
        
        if os.path.exists(pid_file_path):
             try:
                with open(pid_file_path, 'r') as f: pid = int(f.read().strip())
                os.kill(pid, 0)
                print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' appears to be running (PID: {pid}).", file=sys.stderr)
                print(f"{YELLOW}*{RESET} INFO: Por favor, pare a VM com o comando 'stop' antes de remover.")
                sys.exit(1)
             except (OSError, ValueError, TypeError):
                pass 

        files_to_delete = []
        if disk_path and os.path.exists(disk_path): files_to_delete.append(disk_path)
        if nvram_path and os.path.exists(nvram_path): files_to_delete.append(nvram_path)
        if os.path.exists(conf_path): files_to_delete.append(conf_path)
        if os.path.exists(pid_file_path): files_to_delete.append(pid_file_path)
            
        if not files_to_delete:
            print(f"{YELLOW}*{RESET} ATTENTION: No files found for guest '{args.guest_name}'. Nothing to do.", file=sys.stderr)
            sys.exit(0)
            
        print(f"{YELLOW}*{RESET} ATTENTION: The following files will be {RED}PERMANENTLY DELETED{YELLOW}:{RESET}")
        for f in files_to_delete:
            print(f"  - {CYAN}{f}{RESET}")
            
        try:
            response = input(f"    {YELLOW}Are you sure you want to continue? [y/N]: {RESET}").strip().lower()
        except EOFError: response = 'n'
            
        if response not in ('y', 'yes'):
            print(f"{RED}*{RESET} ERROR: Operation aborted by user.", file=sys.stderr)
            sys.exit(1)
            
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
        
        sys.exit(0) 


    # --- Lógica do 'stop' ---
    if args.command == 'stop':
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"
        print(f"{GREEN}*{RESET} INFO: Attempting to stop VM: {YELLOW}{args.guest_name}{RESET}")

        if not os.path.exists(pid_file_path):
            config = load_vm_config(args.guest_name)
            if not config:
                print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' não está definida.", file=sys.stderr)
            else:
                print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' já está parada (PID file not found).", file=sys.stderr)
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
            os.kill(pid, 15) 
            print(f"{GREEN}*{RESET} INFO: Shutdown signal sent. A VM deve desligar em breve.")
            try: os.remove(pid_file_path)
            except OSError as e: print(f"{YELLOW}*{RESET} ATTENTION: Could not remove PID file: {e}", file=sys.stderr)
        except OSError as e:
            print(f"{RED}*{RESET} ERROR: Failed to send signal: {e}", file=sys.stderr)
            if e.errno == 3: 
                print(f"{YELLOW}*{RESET} ATTENTION: Process {pid} not found. Removing stale PID file.", file=sys.stderr)
                try: os.remove(pid_file_path)
                except OSError as e2: print(f"{RED}*{RESET} ERROR: Failed to remove stale PID file: {e2}", file=sys.stderr)
            sys.exit(1)
        
        sys.exit(0)
        
    
    # --- Lógica 'copy' ---
    if args.command == 'copy':
        print(f"{GREEN}*{RESET} INFO: Attempting to clone VM {CYAN}{args.source_name}{RESET} to {CYAN}{args.dest_name}{RESET}...")

        if args.source_name == args.dest_name:
            print(f"{RED}*{RESET} ERROR: Source and destination names cannot be the same.", file=sys.stderr)
            sys.exit(1)

        source_config = load_vm_config(args.source_name)
        if not source_config:
            print(f"{RED}*{RESET} ERROR: Source VM '{args.source_name}' is not defined.", file=sys.stderr)
            sys.exit(1)
            
        if os.path.exists(os.path.join(DEFAULT_CONF_DIR, f"{args.dest_name}.conf")):
            print(f"{RED}*{RESET} ERROR: Destination VM '{args.dest_name}' is already defined.", file=sys.stderr)
            sys.exit(1)

        src_disk_path = source_config.get('disk')
        src_pid_path = f"{DEFAULT_STATE_DIR}/{args.source_name}.pid"
        dest_disk_path = os.path.join(DEFAULT_IMG_DIR, f"{args.dest_name}.qcow2")
        dest_nvram_path = os.path.join(DEFAULT_NVRAM_DIR, f"{args.dest_name}_VARS.fd")

        if os.path.exists(src_pid_path):
            try:
                with open(src_pid_path, 'r') as f: pid = int(f.read().strip())
                os.kill(pid, 0)
                print(f"{RED}*{RESET} ERROR: Source VM '{args.source_name}' is running (PID: {pid}).", file=sys.stderr)
                print(f"{YELLOW}*{RESET} INFO: Please stop the VM with 'stop' before cloning.")
                sys.exit(1)
            except (OSError, ValueError, TypeError):
                pass 

        if not src_disk_path or not os.path.exists(src_disk_path):
            print(f"{RED}*{RESET} ERROR: Source disk not found at: {CYAN}{src_disk_path}{RESET}", file=sys.stderr)
            sys.exit(1)
        if os.path.exists(dest_disk_path):
             print(f"{RED}*{RESET} ERROR: Destination disk file already exists: {CYAN}{dest_disk_path}{RESET}", file=sys.stderr)
             sys.exit(1)

        try:
            print(f"{GREEN}*{RESET} INFO: Copying disk...")
            print(f"{CYAN}*{RESET} EXECUTING: copy {src_disk_path} to {dest_disk_path}")
            shutil.copyfile(src_disk_path, dest_disk_path)
            
            print(f"{GREEN}*{RESET} INFO: Creating new NVRAM for destination...")
            if not create_nvram_file(args.dest_name, dest_nvram_path):
                raise Exception("Failed to create new NVRAM file.")
            
            print(f"{GREEN}*{RESET} INFO: Creating new configuration file...")
            new_config_data = source_config.copy()
            new_config_data['disk'] = dest_disk_path
            new_config_data['nvram'] = dest_nvram_path
            
            # <<< [MODIFICAÇÃO] Gerar um NOVO mac para o clone >>>
            new_mac = generate_random_mac()
            print(f"{GREEN}*{RESET} INFO: Generating new MAC for clone: {CYAN}{new_mac}{RESET}")
            new_config_data['mac'] = new_mac
                
            if not write_vm_config(args.dest_name, new_config_data):
                raise Exception("Failed to write new VM config file.")

            print(f"\n{GREEN}*{RESET} SUCCESS: VM '{args.source_name}' successfully cloned to '{args.dest_name}'.")
        except Exception as e:
            print(f"\n{RED}*{RESET} ERROR: Failed during clone operation: {e}", file=sys.stderr)
            print(f"{YELLOW}*{RESET} ATTENTION: Cleaning up partial files...")
            if os.path.exists(dest_disk_path):
                try: os.remove(dest_disk_path)
                except OSError as e2: print(f"{RED}*{RESET} ERROR: Cleanup of partial disk failed: {e2}", file=sys.stderr)
            if os.path.exists(dest_nvram_path):
                try: os.remove(dest_nvram_path)
                except OSError as e2: print(f"{RED}*{RESET} ERROR: Cleanup of partial NVRAM failed: {e2}", file=sys.stderr)
            if os.path.exists(os.path.join(DEFAULT_CONF_DIR, f"{args.dest_name}.conf")):
                try: os.remove(os.path.join(DEFAULT_CONF_DIR, f"{args.dest_name}.conf"))
                except OSError as e2: print(f"{RED}*{RESET} ERROR: Cleanup of partial config failed: {e2}", file=sys.stderr)
            sys.exit(1)
        
        sys.exit(0) 

    
    # =====================================================================
    # --- Lógica 'new' e 'run' ---
    # =====================================================================
    
    final_disk = None
    final_nvram = None
    final_smp = None
    final_mem = None
    final_bridge = None
    final_mac = None
    final_headless = False
    is_install_boot = False


    # --- Lógica para 'new' ---
    if args.command == 'new':
        print(f"{GREEN}*{RESET} INFO: 'New VM' mode enabled for {GREEN}{args.guest_name}{RESET}")
        
        if os.path.exists(os.path.join(DEFAULT_CONF_DIR, f"{args.guest_name}.conf")):
            print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' is already defined.", file=sys.stderr)
            print(f"{YELLOW}*{RESET} INFO: Use 'remove' to delete it or 'run' to start it.")
            sys.exit(1)
        
        if args.disk:
            disk_path = args.disk
        else:
            disk_path = os.path.join(DEFAULT_IMG_DIR, f"{args.guest_name}.qcow2")
            print(f"{YELLOW}*{RESET} ATTENTION: --disk not specified. Defaulting to: {CYAN}{disk_path}{RESET}")
            os.makedirs(DEFAULT_IMG_DIR, 0o755, exist_ok=True)

        nvram_path = os.path.join(DEFAULT_NVRAM_DIR, f"{args.guest_name}_VARS.fd")
        
        if os.path.exists(disk_path) or os.path.exists(nvram_path):
            print(f"{YELLOW}*{RESET} ATTENTION: Existing files found. These will be {RED}OVERWRITTEN{YELLOW}:{RESET}")
            if os.path.exists(disk_path): print(f"    - Disk:   {CYAN}{disk_path}{RESET}")
            if os.path.exists(nvram_path): print(f"    - NVRAM:  {CYAN}{nvram_path}{RESET}")
            try:
                response = input(f"    {YELLOW}Do you want to delete them and continue? [y/N]: {RESET}").strip().lower()
            except EOFError: response = 'n'
            if response not in ('y', 'yes'):
                print(f"{RED}*{RESET} ERROR: Aborting operation.", file=sys.stderr)
                sys.exit(1)
            try:
                if os.path.exists(disk_path): os.remove(disk_path)
                if os.path.exists(nvram_path): os.remove(nvram_path)
            except OSError as e: 
                print(f"{RED}*{RESET} ERROR: Failed to delete existing files: {e}", file=sys.stderr); sys.exit(1)
        
        if not os.path.exists(args.iso):
            print(f"{RED}*{RESET} ERROR: ISO file not found at: {YELLOW}{args.iso}{RESET}", file=sys.stderr)
            sys.exit(1)

        print(f"{GREEN}*{RESET} INFO: Creating new disk {GREEN}{disk_path}{RESET} with size {GREEN}{args.size}{RESET}...")
        create_cmd = [QEMU_IMG_BINARY, 'create', '-f', 'qcow2', disk_path, args.size]
        if run_command(create_cmd) != 0:
             print(f"{RED}*{RESET} ERROR: qemu-img create failed.", file=sys.stderr)
             sys.exit(1)

        if not create_nvram_file(args.guest_name, nvram_path):
            print(f"{RED}*{RESET} ERROR: Failed to create NVRAM file.", file=sys.stderr)
            os.remove(disk_path) 
            sys.exit(1)
            
        # <<< [MODIFICAÇÃO] Lógica de geração de MAC >>>
        if args.mac:
            print(f"{GREEN}*{RESET} INFO: Using provided MAC address: {CYAN}{args.mac}{RESET}")
            final_mac_for_config = args.mac
        else:
            final_mac_for_config = generate_random_mac()
            print(f"{GREEN}*{RESET} INFO: No MAC provided. Generated new MAC: {CYAN}{final_mac_for_config}{RESET}")
        
        print(f"{GREEN}*{RESET} INFO: Registering new VM definition...")
        config_data = {
            'disk': disk_path,
            'nvram': nvram_path,
            'mem': args.mem,
            'smp': args.smp,
            'bridge': args.bridge,
            'headless': 'false',
            'mac': final_mac_for_config  # Salva o MAC (fornecido ou gerado)
        }
            
        if not write_vm_config(args.guest_name, config_data):
            print(f"{RED}*{RESET} ERROR: Failed to write config file.", file=sys.stderr)
            os.remove(disk_path)
            os.remove(nvram_path)
            sys.exit(1)
            
        final_disk = disk_path
        final_nvram = nvram_path
        final_smp = args.smp
        final_mem = args.mem
        final_bridge = args.bridge
        final_mac = final_mac_for_config # Usa o MAC (fornecido ou gerado)
        final_headless = False 
        is_install_boot = True
        
        print(f"{YELLOW}*{RESET} ATTENTION: A VM será iniciada em FOREGROUND para instalação.")

            
    # --- Lógica para 'run' ---
    elif args.command == 'run':
        print(f"{GREEN}*{RESET} INFO: 'Run VM' mode enabled for {GREEN}{args.guest_name}{RESET}")

        config = load_vm_config(args.guest_name)
        if not config:
            print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' is not defined.", file=sys.stderr)
            print(f"{YELLOW}*{RESET} INFO: Use 'new' to create it or 'list' to see defined VMs.")
            sys.exit(1)

        os.makedirs(DEFAULT_STATE_DIR, 0o755, exist_ok=True)
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"

        if os.path.exists(pid_file_path):
            try:
                with open(pid_file_path, 'r') as f:
                    pid = int(f.read().strip())
                os.kill(pid, 0)
                print(f"{RED}*{RESET} ERROR: VM '{args.guest_name}' appears to be running (PID: {pid}).", file=sys.stderr)
                print(f"{YELLOW}*{RESET} INFO: Use 'stop' para pará-la.")
                sys.exit(1)
            except (OSError, ValueError, TypeError):
                print(f"{YELLOW}*{RESET} ATTENTION: Removing stale PID file {CYAN}{pid_file_path}{RESET}")
                try: os.remove(pid_file_path)
                except OSError as e: print(f"{RED}*{RESET} ERROR: Failed to remove stale PID file: {e}", file=sys.stderr)

        final_disk = args.disk or config.get('disk')
        final_nvram = config.get('nvram') 
        final_smp = args.smp or config.get('smp') or DEFAULT_SMP
        final_mem = args.mem or config.get('mem') or DEFAULT_MEM
        final_bridge = args.bridge or config.get('bridge') or DEFAULT_BRIDGE
        final_mac = args.mac or config.get('mac')
        
        if args.headless: 
            final_headless = True
        else: 
            config_value = config.get('headless', 'false')
            final_headless = str(config_value).lower() == 'true'

        if not final_disk or not os.path.exists(final_disk):
            print(f"{RED}*{RESET} ERROR: Disk file not found at: {YELLOW}{final_disk}{RESET}", file=sys.stderr)
            sys.exit(1)
        if not final_nvram or not os.path.exists(final_nvram):
            print(f"{RED}*{RESET} ERROR: NVRAM file not found at: {YELLOW}{final_nvram}{RESET}", file=sys.stderr)
            sys.exit(1)
            
        if args.iso:
            if not os.path.exists(args.iso):
                print(f"{RED}*{RESET} ERROR: ISO file not found at: {YELLOW}{args.iso}{RESET}", file=sys.stderr)
                sys.exit(1)
            print(f"{GREEN}*{RESET} INFO: ISO provided. Setting as primary boot device.")
            is_install_boot = True
        else:
            is_install_boot = False
            
        print(f"{YELLOW}*{RESET} ATTENTION: A VM será iniciada em BACKGROUND.")


    # --- [LÓGICA COMUM] Construção e Execução do Comando QEMU ---

    if not os.path.exists(OVMF_CODE_PATH):
        print(f"{RED}*{RESET} ERROR: Base OVMF CODE file not found: {YELLOW}{OVMF_CODE_PATH}{RESET}", file=sys.stderr)
        sys.exit(1)

    qemu_command = [
        QEMU_BINARY,
        '-enable-kvm',
        '-cpu', 'host',
        '-smp', final_smp,
        '-m', final_mem,
        '-drive', f'file={final_disk},if=virtio,format=qcow2', 
        '-netdev', f'tap,id=net0,br={final_bridge},helper={QEMU_BRIDGE_HELPER}',
        '-drive', f'if=pflash,format=raw,readonly=on,file={OVMF_CODE_PATH}',
        '-drive', f'if=pflash,format=raw,file={final_nvram}',
    ]

    net_device_str = 'virtio-net-pci,netdev=net0'
    if final_mac:
        if not re.match(r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$', final_mac):
            print(f"{RED}*{RESET} ERROR: Invalid MAC address format ({final_mac}).", file=sys.stderr)
            sys.exit(1)
        print(f"{GREEN}*{RESET} INFO: Using custom MAC address: {CYAN}{final_mac}{RESET}")
        net_device_str += f',mac={final_mac}'
    qemu_command.extend(['-device', net_device_str])

    if final_headless:
        print(f"{GREEN}*{RESET} INFO: Headless mode enabled. Adding {CYAN}-vga none -display none{RESET}.")
        qemu_command.extend(['-vga', 'none', '-display', 'none'])
    else:
        qemu_command.append('-device')
        qemu_command.append('virtio-vga')

    if args.command == 'run':
        pid_file_path = f"{DEFAULT_STATE_DIR}/{args.guest_name}.pid"
        qemu_command.extend([
            '-daemonize',
            '-pidfile', pid_file_path
        ])

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

    print(f"\n{GREEN}*{RESET} INFO: Final command to be executed:")
    print(' '.join(qemu_command))
    print("-" * 70)

    try:
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