#!/usr/bin/env python3

import subprocess
import argparse
import sys
import os
import shutil

# --- ANSI Color Codes ---
GREEN = '\033[32m'
RED = '\033[31m'
YELLOW = '\033[33m'
RESET = '\033[0m'
CYAN = '\033[36m'

# --- Global Constants ---
DEFAULT_IMG_DIR = "/var/lib/libvirt/images"
DEFAULT_NVRAM_DIR = "/var/lib/libvirt/qemu/nvram"
NVRAM_TEMPLATES = [
    '/usr/share/OVMF/OVMF_VARS_4M.fd',
    '/usr/share/OVMF/OVMF_VARS.fd'
]
# Caminhos OVMF (agora separados)
OVMF_CODE_DEFAULT = '/usr/share/OVMF/OVMF_CODE_4M.fd'
OVMF_CODE_SECBOOT = '/usr/share/OVMF/OVMF_CODE_4M.secboot.fd'


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
    print(f"\n{GREEN}*{RESET} {CYAN}base_builder.py: QEMU VM Manager{RESET}")
    print(f"\n{YELLOW}ATTENTION: You must specify an operation mode: 'new', 'run', 'list', or 'remove'.{RESET}\n")
    
    print(f"  {GREEN}To create a new VM:{RESET}")
    print(f"    Use the {CYAN}new{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./base_builder.py new MyVM --size 20G --iso /path/to/install.iso\n")
    
    print(f"  {GREEN}To run an existing VM:{RESET}")
    print(f"    Use the {CYAN}run{RESET} command (disk path is optional):")
    print(f"    {YELLOW}Example:{RESET} ./base_builder.py run MyVM\n")
    
    print(f"  {GREEN}To list available VM disks:{RESET}")
    print(f"    Use the {CYAN}list{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./base_builder.py list\n")

    print(f"  {GREEN}To remove a VM:{RESET}")
    print(f"    Use the {CYAN}remove{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./base_builder.py remove MyVM\n")

    print(f"For full options on a command, run:\n    {YELLOW}./base_builder.py <command> --help{RESET}\n")

def find_nvram_template():
    """Finds the first available OVMF VARS template."""
    for path in NVRAM_TEMPLATES:
        if os.path.exists(path):
            return path
    return None

def create_nvram_file(guest_name, nvram_dest_path):
    """Copies the OVMF template to the destination NVRAM path."""
    print(f"{GREEN}*{RESET} INFO: Preparing NVRAM file at {CYAN}{nvram_dest_path}{RESET}...")
    
    nvram_template_src = find_nvram_template()
    if not nvram_template_src:
        print(f"{RED}*{RESET} ERROR: Could not find OVMF_VARS template (tried {NVRAM_TEMPLATES})", file=sys.stderr)
        return False

    try:
        print(f"{CYAN}*{RESET} EXECUTING: copy {nvram_template_src} to {nvram_dest_path}")
        shutil.copyfile(nvram_template_src, nvram_dest_path)
        return True
    except Exception as e:
        print(f"{RED}*{RESET} ERROR: Failed to copy NVRAM file: {e}", file=sys.stderr)
        return False

def main():
    
    # --- Parser Principal ---
    parser = argparse.ArgumentParser(
        description="Script to create, run, list, or remove QEMU VMs.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Operation mode')

    # --- Sub-comando 'new' ---
    new_parser = subparsers.add_parser('new', help='Create a new VM')
    new_parser.add_argument('guest_name', type=str, help="Name for the new guest (e.g., 'SpiralVM').")
    new_parser.add_argument('--iso', type=str, required=True, help="Path to the ISO image (Mandatory for new).")
    new_parser.add_argument('--size', type=str, required=True, help="Size for new disk (e.g., 20G) (Mandatory for new).")
    new_parser.add_argument('--disk', type=str, help=f"Full path for new .qcow2. (Defaults to {DEFAULT_IMG_DIR}/<guest_name>.qcow2)")
    new_parser.add_argument('--smp', type=str, default='2', help="Number of CPU cores. Default: 2")
    new_parser.add_argument('--mem', type=str, default='2G', help="Amount of memory. Default: 2G")
    new_parser.add_argument('--bridge', type=str, default='br_tap112', help="Network bridge interface. Default: br_tap112")
    new_parser.add_argument('--secboot', action='store_true', help='Enable Secure Boot (uses ...secboot.fd)') # NOVO
    
    # --- Sub-comando 'run' ---
    run_parser = subparsers.add_parser('run', help='Run an existing VM')
    run_parser.add_argument('guest_name', type=str, help="Name of the guest to run (e.g., 'SpiralVM').")
    run_parser.add_argument('--disk', type=str, help=f"Path to the .qcow2 disk. (Optional, defaults to {DEFAULT_IMG_DIR}/<guest_name>.qcow2)")
    run_parser.add_argument('--iso', type=str, help="Path to an ISO image for live boot or repair.")
    run_parser.add_argument('--smp', type=str, default='2', help="Number of CPU cores. Default: 2")
    run_parser.add_argument('--mem', type=str, default='2G', help="Amount of memory. Default: 2G")
    run_parser.add_argument('--bridge', type=str, default='br_tap112', help="Network bridge interface. Default: br_tap112")
    run_parser.add_argument('--secboot', action='store_true', help='Enable Secure Boot (uses ...secboot.fd)') # NOVO

    # --- Sub-comando 'remove' ---
    remove_parser = subparsers.add_parser('remove', help='Remove a VM (disk and NVRAM)')
    remove_parser.add_argument('guest_name', type=str, help="Name of the guest to remove (e.g., 'SpiralVM').")
    remove_parser.add_argument('--disk', type=str, help=f"Path to the .qcow2 disk. (Optional, defaults to {DEFAULT_IMG_DIR}/<guest_name>.qcow2)")

    # --- Sub-comando 'list' ---
    list_parser = subparsers.add_parser('list', help='List available VM disks in the default directory')

    # --- Lógica de Validação ---
    
    if len(sys.argv) == 1:
        print_custom_help()
        sys.exit(0)

    args = parser.parse_args()

    # --- Lógica do 'list' ---
    if args.command == 'list':
        print(f"{GREEN}*{RESET} INFO: Checking for VM disks in {CYAN}{DEFAULT_IMG_DIR}{RESET}...")
        if not os.path.isdir(DEFAULT_IMG_DIR):
            print(f"{RED}*{RESET} ERROR: Default directory not found: {YELLOW}{DEFAULT_IMG_DIR}{RESET}", file=sys.stderr)
            sys.exit(1)
        
        qcow2_files = [f for f in os.listdir(DEFAULT_IMG_DIR) if f.endswith('.qcow2')]
        
        if not qcow2_files:
            print(f"{YELLOW}*{RESET} ATTENTION: No .qcow2 disk images found.", file=sys.stderr)
            sys.exit(0)
            
        print(f"{GREEN}*{RESET} INFO: Found {len(qcow2_files)} disk(s):")
        for f in qcow2_files:
            print(f"  - {CYAN}{f}{RESET}")
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
        
        files_to_delete = []
        if os.path.exists(disk_path):
            files_to_delete.append(disk_path)
        if os.path.exists(nvram_path):
            files_to_delete.append(nvram_path)
            
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

    
    # --- Lógica para 'new' e 'run' ---
    
    is_install_boot = False
    
    # Definir caminhos NVRAM (comuns a 'new' e 'run')
    if not os.path.isdir(DEFAULT_NVRAM_DIR):
         print(f"{RED}*{RESET} ERROR: NVRAM directory not found: {YELLOW}{DEFAULT_NVRAM_DIR}{RESET}", file=sys.stderr)
         sys.exit(1)
    nvram_path = f"{DEFAULT_NVRAM_DIR}/{args.guest_name}_VARS.fd"

    # --- INÍCIO DA LÓGICA SECBOOT ---
    # Determinar qual arquivo OVMF_CODE usar
    if args.secboot:
        print(f"{GREEN}*{RESET} INFO: Secure Boot enabled.")
        ovmf_code_path = OVMF_CODE_SECBOOT
    else:
        ovmf_code_path = OVMF_CODE_DEFAULT

    # Validar OVMF_CODE principal
    if not os.path.exists(ovmf_code_path):
        print(f"{RED}*{RESET} ERROR: Base OVMF CODE file not found: {YELLOW}{ovmf_code_path}{RESET}", file=sys.stderr)
        if args.secboot:
             print(f"{RED}*{RESET} ERROR: Is the 'ovmf-ia32-x64-secboot' package installed?", file=sys.stderr)
        sys.exit(1)
    # --- FIM DA LÓGICA SECBOOT ---


    if args.command == 'new':
        print(f"{GREEN}*{RESET} INFO: 'New VM' mode enabled for {GREEN}{args.guest_name}{RESET}")
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
        create_cmd = ['qemu-img', 'create', '-f', 'qcow2', disk_path, args.size]
        if run_command(create_cmd) != 0:
             print(f"{RED}*{RESET} ERROR: qemu-img create failed.", file=sys.stderr)
             sys.exit(1)

        # 5. Criar NVRAM
        if not create_nvram_file(args.guest_name, nvram_path):
            print(f"{RED}*{RESET} ERROR: Failed to create NVRAM file.", file=sys.stderr)
            sys.exit(1)
            
    elif args.command == 'run':
        print(f"{GREEN}*{RESET} INFO: 'Run VM' mode enabled for {GREEN}{args.guest_name}{RESET}")

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
        'qemu-system-x86_64',
        '-enable-kvm',
        '-cpu', 'host',
        '-smp', args.smp,
        '-m', args.mem,
        '-drive', f'file={disk_path},if=virtio,format=qcow2', 
        '-device', 'virtio-net-pci,netdev=net0',
        '-netdev', f'tap,id=net0,br={args.bridge},helper=/usr/lib/qemu/qemu-bridge-helper',
        '-device', 'virtio-vga',
        # MODIFICADO: usa o caminho do OVMF selecionado (normal ou secboot)
        '-drive', f'if=pflash,format=raw,readonly=on,file={ovmf_code_path}',
        '-drive', f'if=pflash,format=raw,file={nvram_path}',
    ]

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
             
    except KeyboardInterrupt:
        print(f"\n{YELLOW}*{RESET} ATTENTION: VM boot interrupted by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()