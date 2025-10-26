#!/usr/bin/env python3

import subprocess
import argparse
import sys
import os
import shutil  # Importado para operações de cópia de arquivo

# --- ANSI Color Codes ---
GREEN = '\033[32m'
RED = '\033[31m'
YELLOW = '\033[33m'
RESET = '\033[0m'
CYAN = '\033[36m'

def run_command(cmd_list):
    """Helper function to run external commands."""
    try:
        print(f"{CYAN}*{RESET} EXECUTING: {' '.join(cmd_list)}")
        # Retorna o código de saída para verificação
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
    print(f"\n{YELLOW}ATTENTION: You must specify an operation mode: 'new' or 'run'.{RESET}\n")
    
    print(f"  {GREEN}To create a new VM:{RESET}")
    print(f"    Use the {CYAN}new{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./base_builder.py new MyVM --size 20G --iso /path/to/install.iso\n")
    
    print(f"  {GREEN}To run an existing VM:{RESET}")
    print(f"    Use the {CYAN}run{RESET} command:")
    print(f"    {YELLOW}Example:{RESET} ./base_builder.py run MyVM --disk /path/to/vm.qcow2\n")
    
    print(f"For a full list of options for a command, run:\n    {YELLOW}./base_builder.py new --help{RESET} or {YELLOW}./base_builder.py run --help{RESET}\n")

def main():
    
    # --- Parser Principal ---
    parser = argparse.ArgumentParser(
        description="Script to create or run QEMU VMs.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Operation mode')
    
    # --- Sub-comando 'new' ---
    new_parser = subparsers.add_parser('new', help='Create a new VM')
    new_parser.add_argument('guest_name', type=str, help="Name for the new guest (e.g., 'SpiralVM').")
    new_parser.add_argument('--iso', type=str, required=True, help="Path to the ISO image (Mandatory for new).")
    new_parser.add_argument('--size', type=str, required=True, help="Size for new disk (e.g., 20G) (Mandatory for new).")
    new_parser.add_argument('--disk', type=str, help="Full path for new .qcow2. (Defaults to /var/lib/libvirt/images/<guest_name>.qcow2)")
    new_parser.add_argument('--smp', type=str, default='2', help="Number of CPU cores. Default: 2")
    new_parser.add_argument('--mem', type=str, default='2G', help="Amount of memory. Default: 2G")
    new_parser.add_argument('--bridge', type=str, default='br_tap112', help="Network bridge interface. Default: br_tap112")
    
    # --- Sub-comando 'run' ---
    run_parser = subparsers.add_parser('run', help='Run an existing VM')
    run_parser.add_argument('guest_name', type=str, help="Name of the guest to run (e.g., 'SpiralVM').")
    run_parser.add_argument('--disk', type=str, required=True, help="Path to the existing .qcow2 disk (Mandatory for run).")
    run_parser.add_argument('--iso', type=str, help="Path to an ISO image for live boot or repair.")
    run_parser.add_argument('--smp', type=str, default='2', help="Number of CPU cores. Default: 2")
    run_parser.add_argument('--mem', type=str, default='2G', help="Amount of memory. Default: 2G")
    run_parser.add_argument('--bridge', type=str, default='br_tap112', help="Network bridge interface. Default: br_tap112")

    args = parser.parse_args()

    # --- Lógica de Validação ---
    
    if not args.command:
        print_custom_help()
        sys.exit(0)

    is_install_boot = False
    disk_path = args.disk
    
    if args.command == 'new':
        # --- MODO DE CRIAÇÃO ---
        print(f"{GREEN}*{RESET} INFO: 'New VM' mode enabled for {GREEN}{args.guest_name}{RESET}")
        is_install_boot = True
        
        # --- 1. Definir Caminhos ---
        
        # Lógica do Disco (Default)
        if not disk_path:
            default_dir = "/var/lib/libvirt/images"
            disk_path = f"{default_dir}/{args.guest_name}.qcow2"
            
            print(f"{YELLOW}*{RESET} ATTENTION: --disk not specified. Defaulting to: {CYAN}{disk_path}{RESET}")
            
            if not os.path.isdir(default_dir):
                print(f"{RED}*{RESET} ERROR: Default directory not found: {YELLOW}{default_dir}{RESET}", file=sys.stderr)
                sys.exit(1)
        
        # Lógica da NVRAM
        nvram_dir = "/var/lib/libvirt/qemu/nvram"
        nvram_dest_path = f"{nvram_dir}/{args.guest_name}_VARS.fd"
        
        if not os.path.isdir(nvram_dir):
             print(f"{RED}*{RESET} ERROR: NVRAM directory not found: {YELLOW}{nvram_dir}{RESET}", file=sys.stderr)
             sys.exit(1)

        # --- 2. Verificar se arquivos existem (NOVA LÓGICA) ---
        disk_exists = os.path.exists(disk_path)
        nvram_exists = os.path.exists(nvram_dest_path)
        
        if disk_exists or nvram_exists:
            print(f"{YELLOW}*{RESET} ATTENTION: Existing files found for guest '{args.guest_name}':")
            if disk_exists:
                print(f"    - Disk:   {CYAN}{disk_path}{RESET}")
            if nvram_exists:
                print(f"    - NVRAM:  {CYAN}{nvram_dest_path}{RESET}")
            
            try:
                response = input(f"    {YELLOW}Do you want to delete them and continue? [y/N]: {RESET}").strip().lower()
            except EOFError:
                response = 'n' # Tratar Ctrl+D como "Não"
            
            if response not in ('y', 'yes'):
                print(f"{RED}*{RESET} ERROR: Aborting operation. Files not removed.", file=sys.stderr)
                sys.exit(1)

            # Usuário disse sim. Excluir arquivos.
            if disk_exists:
                print(f"{YELLOW}*{RESET} ATTENTION: Deleting existing disk file: {CYAN}{disk_path}{RESET}")
                try:
                    os.remove(disk_path)
                except OSError as e:
                    print(f"{RED}*{RESET} ERROR: Failed to delete disk file: {e}", file=sys.stderr)
                    sys.exit(1)

            if nvram_exists:
                print(f"{YELLOW}*{RESET} ATTENTION: Deleting existing NVRAM file: {CYAN}{nvram_dest_path}{RESET}")
                try:
                    os.remove(nvram_dest_path)
                except OSError as e:
                    print(f"{RED}*{RESET} ERROR: Failed to delete NVRAM file: {e}", file=sys.stderr)
                    sys.exit(1)
        
        # --- 3. Validação da ISO (obrigatória) ---
        if not os.path.exists(args.iso):
            print(f"{RED}*{RESET} ERROR: ISO file not found at: {YELLOW}{args.iso}{RESET}", file=sys.stderr)
            sys.exit(1)

        # --- 4. Criar Disco ---
        print(f"{GREEN}*{RESET} INFO: Creating new disk {GREEN}{disk_path}{RESET} with size {GREEN}{args.size}{RESET}...")
        create_cmd = ['qemu-img', 'create', '-f', 'qcow2', disk_path, args.size]
        if run_command(create_cmd) != 0:
             print(f"{RED}*{RESET} ERROR: qemu-img create failed.", file=sys.stderr)
             sys.exit(1)

        # --- 5. Criar arquivo NVRAM ---
        print(f"{GREEN}*{RESET} INFO: Preparing NVRAM file...")
        
        nvram_template_paths = ['/usr/share/OVMF/OVMF_VARS_4M.fd', '/usr/share/OVMF/OVMF_VARS.fd']
        nvram_template_src = None
        for path in nvram_template_paths:
            if os.path.exists(path):
                nvram_template_src = path
                break

        if not nvram_template_src:
            print(f"{RED}*{RESET} ERROR: Could not find OVMF_VARS template (tried {nvram_template_paths})", file=sys.stderr)
            sys.exit(1)

        try:
            print(f"{CYAN}*{RESET} EXECUTING: copy {nvram_template_src} to {nvram_dest_path}")
            shutil.copyfile(nvram_template_src, nvram_dest_path)
        except Exception as e:
            print(f"{RED}*{RESET} ERROR: Failed to copy NVRAM file: {e}", file=sys.stderr)
            sys.exit(1)
        
    elif args.command == 'run':
        # --- MODO DE EXECUÇÃO ---
        if not os.path.exists(args.disk):
            print(f"{RED}*{RESET} ERROR: qcow2 file not found at: {YELLOW}{args.disk}{RESET}", file=sys.stderr)
            sys.exit(1)

        if args.iso:
            if not os.path.exists(args.iso):
                print(f"{RED}*{RESET} ERROR: ISO file not found at: {YELLOW}{args.iso}{RESET}", file=sys.stderr)
                sys.exit(1)
            print(f"{GREEN}*{RESET} INFO: ISO provided. Setting as primary boot device.")
            is_install_boot = True
        
        # No modo 'run', o disk_path é apenas o args.disk
        disk_path = args.disk

    # --- Construção do Comando QEMU ---

    # Definir caminho do NVRAM (agora o arquivo deve existir)
    nvram_path = f"/var/lib/libvirt/qemu/nvram/{args.guest_name}_VARS.fd"
    
    if not os.path.exists(nvram_path):
         print(f"{RED}*{RESET} ERROR: NVRAM file not found at: {YELLOW}{nvram_path}{RESET}", file=sys.stderr)
         print(f"{RED}*{RESET} ERROR: (If this is a new VM, something went wrong. If running, check the path.)", file=sys.stderr)
         sys.exit(1)

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
        '-drive', 'if=pflash,format=raw,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.fd',
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