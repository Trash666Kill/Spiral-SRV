#!/usr/bin/env python3

import argparse
import configparser
import os
import random
# import secrets # Não é mais necessário com sasl=off
import shlex
import socket
import subprocess
import sys
import time
import uuid
from pathlib import Path
import shutil

# --- Configuration Constants ---
VMS_DIR = Path("vms")
GLOBAL_CONF = Path("global.conf")
QEMU_BIN = "qemu-system-x86_64"
SPICE_PORT_MIN = 5900
SPICE_PORT_MAX = 5960
SPICE_PASSWORD_LENGTH = 8 # Mantido caso seja usado por outra coisa, mas não pelo SPICE

# --- ANSI Color Codes for Output ---
COLOR_GREEN = "\033[32m"
COLOR_RED = "\033[31m"
COLOR_YELLOW = "\033[33m"
COLOR_BLUE = "\033[34m" # For informational messages/paths
COLOR_RESET = "\033[0m"

# --- Default Content for global.conf ---
DEFAULT_GLOBAL_CONF = f"""
;
; Default global configuration file for vm_manager.py
;
; This file was automatically generated.
; Please review the paths (especially [pools] and [firmware_paths])
; to ensure they match your system configuration.
;

[pools]
; The default storage pool if --pool is not used during 'create'
default = /home/sysop/.virt
Container-A = /mnt/Temp/Container/A/Virt

[network]
; The default network bridge. (Change to your bridge, e.g., br0, virbr0)
bridge = br0

[disks]
; The default disk image format
image_format = qcow2

[hardware]
; Default firmware (uefi or bios)
firmware = uefi
; Default chipset (q35 or i440fx)
chipset = q35

[firmware_paths]
; (IMPORTANT) Verify these paths are correct for your system!
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

# --- Formatted Print Functions ---
def _print_info(message):
    print(f"{COLOR_GREEN}*{COLOR_RESET} {message}")

def _print_warn(message):
    print(f"{COLOR_YELLOW}*{COLOR_RESET} {message}")

def _print_error(message):
    # Allows embedding color codes within the message itself for path highlighting etc.
    print(f"{COLOR_RED}*{COLOR_RESET} {message}", file=sys.stderr)

def _generate_mac() -> str:
    """Generates a random MAC address with QEMU prefix (52:54:00)."""
    mac = [0x52, 0x54, 0x00,
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff),
           random.randint(0x00, 0xff)]
    return ':'.join(f"{x:02x}" for x in mac)

def get_vm_config(vm_name: str) -> configparser.ConfigParser:
    """Reads global.conf and the VM-specific .conf file in order."""
    conf_file = VMS_DIR / f"{vm_name}.conf"
    if not conf_file.exists():
        _print_error(f"ERROR: Configuration file {COLOR_BLUE}{conf_file}{COLOR_RED} not found.")
        sys.exit(1)

    config = configparser.ConfigParser()
    try:
        read_files = config.read([GLOBAL_CONF, conf_file])
        # Check if global was actually read, warn if not (but don't fail)
        if str(GLOBAL_CONF) not in read_files:
             _print_warn(f"ATTENTION: Global config '{COLOR_BLUE}{GLOBAL_CONF}{COLOR_YELLOW}' not found or unreadable. Using only VM config.")
        return config
    except configparser.Error as e:
        _print_error(f"ERROR: Could not read configuration files: {e}")
        sys.exit(1)

def get_vm_paths(vm_name: str) -> (Path, Path):
    """Returns the paths for the VM's .pid and .sock files."""
    pid_file = VMS_DIR / f"{vm_name}.pid"
    sock_file = VMS_DIR / f"{vm_name}.sock"
    return pid_file, sock_file

def is_vm_running(pid_file: Path) -> bool:
    """Checks if the VM is running based on the PID file."""
    if not pid_file.exists():
        return False

    try:
        pid = int(pid_file.read_text().strip())
    except (ValueError, FileNotFoundError):
        return False # Corrupt or vanished PID file

    try:
        os.kill(pid, 0) # Send signal 0 to check if process exists
    except ProcessLookupError:
        return False # Process does not exist (stale PID)
    except PermissionError:
        # Process exists but we don't own it (likely root's QEMU process)
        # Since script runs as root, this means it's running.
        return True

    return True # Process exists and we can signal it (unlikely if run as root)

def resolve_image_path(config: configparser.ConfigParser) -> (str, str):
    """Resolves the final image_file path using pool logic."""
    try:
        image_file = config.get("disks", "image_file")
        image_format = config.get("disks", "image_format")
    except configparser.NoOptionError as e:
        _print_error(f"ERROR: Mandatory configuration '{COLOR_YELLOW}{e.option}{COLOR_RED}' not found in section [disks].")
        _print_error(f"Hint: Ensure '{e.option}' is defined in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED} or your VM's .conf file.")
        sys.exit(1)

    if Path(image_file).is_absolute():
        return image_file, image_format

    try:
        pool_name = config.get("disks", "image_pool", fallback="default")
        pool_path_str = config.get("pools", pool_name)
        pool_path = Path(pool_path_str) # Convert to Path object early
    except configparser.NoSectionError:
        _print_error(f"ERROR: Section [pools] not found in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED}")
        sys.exit(1)
    except configparser.NoOptionError:
        _print_error(f"ERROR: Pool '{COLOR_YELLOW}{pool_name}{COLOR_RED}' not defined in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED} [pools]")
        sys.exit(1)
    except Exception as e:
         _print_error(f"ERROR: resolving pool path for pool '{COLOR_YELLOW}{pool_name}{COLOR_RED}': {e}")
         sys.exit(1)


    final_path = pool_path / image_file
    return str(final_path), image_format

def _show_vm_details(vm_name: str):
    """Fetches and displays detailed information for a single VM."""
    try:
        config = get_vm_config(vm_name)
    except SystemExit:
        return # Error already printed by get_vm_config

    pid_file, sock_file = get_vm_paths(vm_name)
    running = is_vm_running(pid_file)

    print(f"--- VM Details: {COLOR_BLUE}{vm_name}{COLOR_RESET} ---")

    status_str = f"{COLOR_GREEN}Running{COLOR_RESET}" if running else f"{COLOR_RED}Stopped{COLOR_RESET}"
    print(f"\n{COLOR_BLUE}[Execution Status]{COLOR_RESET}")
    print(f"  State:       {status_str}")
    if running:
        try:
            print(f"  PID:         {pid_file.read_text().strip()}")
            # Use resolve() to get absolute path for clarity
            print(f"  Monitor:     {sock_file.resolve()}")
        except FileNotFoundError:
            _print_warn("ATTENTION: PID/Monitor files missing, attempting cleanup...")
            if pid_file.exists(): pid_file.unlink()
            if sock_file.exists(): sock_file.unlink()
        except Exception as e:
             _print_error(f"ERROR: Could not read runtime files: {e}")
    else:
        print(f"  PID:         N/A")
        print(f"  Monitor:     N/A")

    print(f"\n{COLOR_BLUE}[Hardware (Configured)]{COLOR_RESET}")
    firmware = config.get('hardware', 'firmware', fallback='bios')
    chipset = config.get('hardware', 'chipset', fallback='N/A (i440fx)')
    vm_uuid = config.get('hardware', 'uuid', fallback='N/A')
    os_type = config.get('hardware', 'os_type', fallback='generic')
    print(f"  Memory:      {config.get('hardware', 'memory', fallback='N/A')}")
    print(f"  SMP (vCPUs):{config.get('hardware', 'smp', fallback='N/A')}")
    print(f"  Firmware:    {firmware.upper()}")
    print(f"  Chipset:     {chipset}")
    print(f"  UUID:        {vm_uuid}")
    print(f"  OS Type:     {os_type}")
    print(f"  CPU (fixed): host")
    print(f"  KVM (fixed): enabled")

    print(f"\n{COLOR_BLUE}[Disks (Resolved)]{COLOR_RESET}")
    try:
        image_path, image_format = resolve_image_path(config)
        print(f"  Pool:        {config.get('disks', 'image_pool', fallback='default')}")
        print(f"  Image:       {image_path}")
        print(f"  Format:      {image_format}")
    except SystemExit:
         print(f"  Image:       (Error resolving path)") # Don't exit here, just report
    except Exception as e:
        _print_error(f"  Image:       (Error resolving: {e})")

    print(f"\n{COLOR_BLUE}[Network (Resolved)]{COLOR_RESET}")
    print(f"  Bridge:      {config.get('network', 'bridge', fallback='N/A')}")
    print(f"  MAC:         {config.get('network', 'mac', fallback='N/A')}")

    print(f"\n{COLOR_BLUE}[Options (Configured)]{COLOR_RESET}")
    extra_flags = config.get('options', 'extra_flags', fallback="None")
    print(f"  Extra Flags: {extra_flags}")

def handle_list(args):
    """Lists all defined VMs or details for a specific VM."""
    if args.vm_name:
        _show_vm_details(args.vm_name)
        return

    _print_info("Defined VMs:")

    vm_files = sorted(list(VMS_DIR.glob("*.conf")))
    if not vm_files:
        _print_info(f"  (No .conf files found in '{COLOR_BLUE}{VMS_DIR}{COLOR_RESET}/')")
        return

    # Find longest name for formatting alignment
    max_len = max(len(f.stem) for f in vm_files) if vm_files else 0

    for conf_file in vm_files:
        vm_name = conf_file.stem
        pid_file, _ = get_vm_paths(vm_name)
        status = f"{COLOR_GREEN}Running{COLOR_RESET}" if is_vm_running(pid_file) else f"{COLOR_RED}Stopped{COLOR_RESET}"
        # Use left-alignment with the max length found
        print(f"  - {vm_name:<{max_len}}    ({status})")

def handle_status(args):
    """Checks and reports the status of a specific VM."""
    vm_name = args.vm_name
    pid_file, _ = get_vm_paths(vm_name)
    status = f"{COLOR_GREEN}Running{COLOR_RESET}" if is_vm_running(pid_file) else f"{COLOR_RED}Stopped{COLOR_RESET}"
    print(f"VM '{COLOR_BLUE}{vm_name}{COLOR_RESET}' is: {status}")

def _build_qemu_command(vm_name: str, config: configparser.ConfigParser, iso_list: list = None, graphical_mode: bool = False, spice_port_arg: int = None) -> tuple[list, int | None, str | None]:
    """
    Internal helper function to build the QEMU command list.
    Returns: Tuple (qemu_cmd_list, spice_port, spice_password)
             spice_port and spice_password are None if SPICE is not automatically configured.
    """
    spice_port = None
    spice_password = None # Definido como None pois sasl=off

    # 1. Resolve image path
    try:
        image_path, image_format = resolve_image_path(config)
    except SystemExit:
        raise # Propagate exit if resolve fails critically

    if not Path(image_path).exists():
        _print_error(f"ERROR: Image file not found: {COLOR_BLUE}{image_path}{COLOR_RED}")
        _print_error("Hint: Use the 'create' command to create this VM and its disk first.")
        sys.exit(1) # Critical error, cannot start VM

    # 2. Base QEMU command
    qemu_cmd = [
        QEMU_BIN,
        "-enable-kvm",
        "-cpu", "host",
        "-smp", config.get("hardware", "smp", fallback="2"),
        "-m", config.get("hardware", "memory", fallback="2G"),
    ]

    if config.has_option("hardware", "uuid"):
        qemu_cmd.extend(["-uuid", config.get("hardware", "uuid")])

    # 3. Chipset
    chipset = config.get("hardware", "chipset", fallback=None)
    if chipset:
        qemu_cmd.extend(["-machine", chipset])

    # 4. Serial Console (for Linux guests)
    os_type = config.get("hardware", "os_type", fallback="generic")
    if os_type == "linux":
        _print_info("OS Type 'linux' detected. Adding serial console (-serial pty)...")
        qemu_cmd.extend(["-serial", "pty"])

    # --- 5. Graphics, SPICE, Boot, Daemonization ---
    extra_flags_str = config.get('options', 'extra_flags', fallback="").strip()
    extra_flags_list = shlex.split(extra_flags_str) if extra_flags_str else []

    # Check for manual graphics/remote display conflicts in extra_flags
    has_manual_vga = any(arg.startswith("-vga") for arg in extra_flags_list)
    has_manual_display = any(arg.startswith("-display") for arg in extra_flags_list)
    has_manual_spice = any(arg.startswith("-spice") for arg in extra_flags_list)
    has_manual_vnc = any(arg.startswith("-vnc") for arg in extra_flags_list)
    has_manual_nographic = "-nographic" in extra_flags_list

    qemu_cmd.extend(["-boot", "order=c"]) # Start with disk boot order

    if graphical_mode:
        _print_info("Graphical Mode requested (--vga).")
        qemu_cmd.extend(["-boot", "menu=on"]) # Add menu=on for graphical modes

        if has_manual_spice or has_manual_vnc or has_manual_display or has_manual_nographic:
            _print_warn("ATTENTION: Manual graphics/display flags (-vga, -display, -spice, -vnc, -nographic) detected in [options]extra_flags.")
            _print_warn("Automatic temporary SPICE configuration will be skipped.")
            qemu_cmd.extend(extra_flags_list) # Apply manual flags
        else:
            # --- Configure Temporary SPICE ---
            _print_info("Configuring temporary SPICE server (no password)...")
            # Port
            if spice_port_arg:
                if SPICE_PORT_MIN <= spice_port_arg <= SPICE_PORT_MAX:
                    spice_port = spice_port_arg
                else:
                    _print_error(f"ERROR: Specified SPICE port {COLOR_YELLOW}{spice_port_arg}{COLOR_RED} is outside the allowed range ({SPICE_PORT_MIN}-{SPICE_PORT_MAX}).")
                    sys.exit(1)
            else:
                spice_port = random.randint(SPICE_PORT_MIN, SPICE_PORT_MAX)
            
            # (sasl=off) - Remove a lógica de 'secret' e 'password-secret'
            qemu_cmd.extend([
                "-spice", f"port={spice_port},addr=0.0.0.0,disable-ticketing=on,sasl=off"
            ])

            # VGA Adapter (conditional) - only if -vga wasn't in extra_flags
            if not has_manual_vga:
                if os_type == 'windows':
                    _print_info("OS Type 'windows': using '-vga qxl' for SPICE.")
                    qemu_cmd.extend(["-vga", "qxl"])
                elif os_type == 'linux':
                    _print_info("OS Type 'linux': using '-vga virtio' for SPICE.")
                    qemu_cmd.extend(["-vga", "virtio"])
                else:
                     _print_warn("ATTENTION: OS Type is 'generic' or unknown. Using default VGA for SPICE (might not be optimal).")
                     # Let QEMU use its default VGA when none is specified
            else:
                 _print_info("Using manual '-vga' configuration from extra_flags.")
                 # Apply extra flags only if they weren't used to disable auto-spice
                 qemu_cmd.extend(extra_flags_list)


    else: # Headless Mode
        _print_info("Headless Mode: Starting in background.")
        qemu_cmd.extend(["-boot", "menu=off"]) # No menu for headless
        qemu_cmd.extend(["-vga", "none"]) # Ensure no virtual GPU
        qemu_cmd.extend(["-display", "none"]) # Force no display output
        pid_file, sock_file = get_vm_paths(vm_name)
        qemu_cmd.extend([
            "-daemonize",
            "-pidfile", str(pid_file),
            "-monitor", f"unix:{sock_file},server,nowait",
        ])
        # Apply extra flags always now (moved outside conditional)


    # 6. Firmware (UEFI/BIOS)
    firmware_type = config.get("hardware", "firmware", fallback="bios")
    if firmware_type.lower() == "uefi":
        if not graphical_mode:
            _print_info("Configuring UEFI mode...")
        try:
            code_path_str = config.get("firmware_paths", "uefi_code")
            vars_template_path_str = config.get("firmware_paths", "uefi_vars_template")
            code_path = Path(code_path_str)
            vars_template_path = Path(vars_template_path_str)
        except (configparser.NoSectionError, configparser.NoOptionError) as e:
            _print_error(f"ERROR: UEFI firmware is set, but [firmware_paths] section is missing or incomplete in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED}")
            sys.exit(1)

        vm_vars_path = VMS_DIR / f"{vm_name}_VARS.fd"
        if not vm_vars_path.exists():
            try:
                # Check if template exists BEFORE trying to copy
                if not vars_template_path.exists():
                    _print_error(f"ERROR: UEFI VARS template file not found: {COLOR_BLUE}{vars_template_path}{COLOR_RED}")
                    _print_error(f"Check the path in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED} [firmware_paths]")
                    sys.exit(1)
                _print_info(f"Copying UEFI VARS template to: {COLOR_BLUE}{vm_vars_path}{COLOR_RESET}")
                shutil.copyfile(vars_template_path, vm_vars_path)
            except Exception as e:
                _print_error(f"ERROR: Failed to copy UEFI VARS file: {e}")
                sys.exit(1)

        # Check if CODE file exists
        if not code_path.exists():
            _print_error(f"ERROR: UEFI CODE file not found: {COLOR_BLUE}{code_path}{COLOR_RED}")
            _print_error(f"Check the path in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED} [firmware_paths]")
            sys.exit(1)

        qemu_cmd.extend([
            "-drive", f"if=pflash,format=raw,readonly=on,file={code_path_str}",
            "-drive", f"if=pflash,format=raw,file={vm_vars_path}"
        ])

    # 7. Main Disk
    qemu_cmd.extend([
        "-drive", f"file={image_path},if=virtio,format={image_format},media=disk"
    ])

    # 8. Network (with MAC)
    try:
        bridge = config.get("network", "bridge")
        mac = config.get("network", "mac", fallback=None)
    except configparser.NoOptionError as e:
         _print_error(f"ERROR: Network option '{COLOR_YELLOW}{e.option}{COLOR_RED}' missing in [network] section.")
         sys.exit(1)

    net_device_str = f"bridge,id=net0,br={bridge}"
    virtio_net_str = f"virtio-net-pci,netdev=net0"

    if mac:
        virtio_net_str += f",mac={mac}"

    qemu_cmd.extend(["-device", virtio_net_str, "-netdev", net_device_str])

    # 9. ISOs (if provided)
    if iso_list:
        _print_info(f"Attaching {len(iso_list)} ISO(s)...")
        qemu_cmd.extend(["-device", "ahci,id=ahci0"]) # Add SATA controller

        for i, iso_path_str in enumerate(iso_list):
            iso_path = Path(iso_path_str)
            if not iso_path.exists():
                _print_error(f"ERROR: ISO file not found: {COLOR_BLUE}{iso_path}{COLOR_RED}")
                sys.exit(1)

            drive_id = f"cdrom_sata_{i}"
            # Define drive without interface, then link device to AHCI bus
            qemu_cmd.extend([
                "-drive", f"file={iso_path_str},id={drive_id},if=none,media=cdrom,readonly=on",
                "-device", f"ide-cd,bus=ahci0.{i},drive={drive_id}" # Connect to AHCI bus port i
            ])

    # 10. Extra Flags (Applied unconditionally if present and not handled above)
    #    Flags related to graphics that were already checked are skipped if needed.
    #    Apply remaining flags.
    if extra_flags_list:
        apply_extra = True
        if graphical_mode and (has_manual_spice or has_manual_vnc or has_manual_display or has_manual_nographic):
            # Already applied these potentially conflicting flags above in graphical mode
            apply_extra = False
        elif graphical_mode and has_manual_vga:
             apply_extra = False # Already applied these potentially conflicting flags above in graphical mode

        if apply_extra:
             # Apply flags not related to auto-spice/vga logic or all flags in headless non-conflicting case
             flags_to_apply = [f for f in extra_flags_list if not (
                 (graphical_mode and (f.startswith("-spice") or f.startswith("-vnc") or f.startswith("-display") or f == "-nographic"))
                 or
                 (graphical_mode and has_manual_vga and f.startswith("-vga")) # Avoid double -vga if manually set
             )]
             if flags_to_apply:
                  flags_str = ' '.join(flags_to_apply)
                  _print_info(f"Applying extra flags: {flags_str}")
                  qemu_cmd.extend(flags_to_apply)


    return qemu_cmd, spice_port, spice_password


def handle_start(args):
    """Starts an existing VM (headless by default, graphical/SPICE with --vga)."""
    vm_name = args.vm_name
    try:
        config = get_vm_config(vm_name)
    except SystemExit:
         return # Error already printed
    pid_file, sock_file = get_vm_paths(vm_name)

    # Check if already running in the target mode
    if not args.vga and is_vm_running(pid_file):
        _print_error(f"ERROR: VM '{COLOR_BLUE}{vm_name}{COLOR_RED}' already appears to be running (headless).")
        sys.exit(1)
    # Warn if switching from headless to graphical/SPICE
    elif args.vga and is_vm_running(pid_file):
        _print_warn(f"ATTENTION: VM '{COLOR_BLUE}{vm_name}{COLOR_YELLOW}' is already running headless.")
        _print_warn("Starting graphically/SPICE may cause conflicts. Continuing in 5 seconds...")
        time.sleep(5)

    try:
        qemu_cmd, spice_port, spice_password = _build_qemu_command(
            vm_name,
            config,
            iso_list=args.iso,
            graphical_mode=args.vga,
            spice_port_arg=args.spice_port # Pass the optional port
        )
    except SystemExit:
        return # Error handled in _build_qemu_command
    except (configparser.NoSectionError, configparser.NoOptionError) as e:
        _print_error(f"ERROR: Missing or invalid configuration option: {e}")
        sys.exit(1)
    except Exception as e:
        _print_error(f"ERROR: Unexpected error building QEMU command: {e}")
        sys.exit(1)

    # print(f"Command: {' '.join(qemu_cmd)}") # Uncomment for debugging

    try:
        if args.vga:
            _print_info(f"Starting VM '{COLOR_BLUE}{vm_name}{COLOR_RESET}' in GRAPHICAL/SPICE mode (foreground)...")
            
            if spice_port:
                 _print_info(f"SPICE server configured on port: {COLOR_YELLOW}{spice_port}{COLOR_RESET}")
                 hostname = socket.gethostname()
                 print(f"Connect using a SPICE client (e.g., remote-viewer spice://{hostname}:{spice_port})")
            elif not (config.has_option('options','extra_flags') and any(f in config.get('options','extra_flags') for f in ['-spice','-vnc','-display','-nographic'])):
                 _print_warn("ATTENTION: No manual SPICE/VNC/display flags found and automatic SPICE disabled.")
                 _print_warn("VM will likely start with default QEMU display (SDL/GTK if available).")

            # Run QEMU directly, wait for it to finish
            subprocess.run(qemu_cmd, check=True)
            _print_info(f"Graphical/SPICE session for '{COLOR_BLUE}{vm_name}{COLOR_RESET}' ended.")
        else:
            _print_info(f"Starting VM '{COLOR_BLUE}{vm_name}{COLOR_RESET}' (headless)...")
            # Run QEMU (daemonized)
            subprocess.run(qemu_cmd, check=True)

            # Verify successful daemonization
            time.sleep(1.5) # Give daemon time to create files
            if is_vm_running(pid_file):
                _print_info(f"VM '{COLOR_BLUE}{vm_name}{COLOR_RESET}' started successfully.")
                try:
                    # Use standard print for details, aligned
                    print(f"  PID:         {pid_file.read_text().strip()}")
                    print(f"  Monitor:     {sock_file.resolve()}")
                except FileNotFoundError:
                     _print_warn("ATTENTION: Could not read PID or resolve Monitor path after start.")
                except Exception as e:
                     _print_error(f"ERROR: reading runtime files after start: {e}")
            else:
                _print_error(f"ERROR: VM failed to start. Check QEMU output or system logs for details.")

    except subprocess.CalledProcessError as e:
        _print_error(f"ERROR: QEMU execution failed.")
    # --- INÍCIO DA CORREÇÃO (Ctrl+C) ---
    except KeyboardInterrupt:
        _print_warn(f"\nGraphical/SPICE session for '{COLOR_BLUE}{vm_name}{COLOR_RESET}' interrupted by user (Ctrl+C).")
        # O QEMU já recebeu o sinal e está terminando, então apenas saímos
    # --- FIM DA CORREÇÃO ---
    except FileNotFoundError:
        _print_error(f"ERROR: Command '{COLOR_BLUE}{QEMU_BIN}{COLOR_RED}' not found. Is QEMU installed and in your PATH?")
        sys.exit(1)
    except Exception as e:
        _print_error(f"ERROR: An unexpected error occurred during QEMU execution: {e}")


def handle_create(args):
    """Creates the .conf, disk image, and starts the graphical installer."""
    vm_name = args.vm_name
    conf_file = VMS_DIR / f"{vm_name}.conf"

    if conf_file.exists():
        _print_error(f"ERROR: VM '{COLOR_BLUE}{vm_name}{COLOR_RED}' already exists ({COLOR_BLUE}{conf_file}{COLOR_RED})")
        _print_error("Use 'start' to run it or 'remove' to delete it first.")
        sys.exit(1)

    pid_file, _ = get_vm_paths(vm_name)
    if is_vm_running(pid_file):
        _print_error(f"ERROR: VM '{COLOR_BLUE}{vm_name}{COLOR_RED}' appears to be running (PID file exists). Please clean up manually.")
        sys.exit(1)

    # 1. Load global defaults
    g_config = configparser.ConfigParser()
    # (Global conf existence checked in main())
    try:
        g_config.read(GLOBAL_CONF)
    except configparser.Error as e:
        _print_error(f"ERROR: reading global config {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED}: {e}")
        sys.exit(1)

    os_profile_section = f"install_defaults_{args.os_type}"
    if not g_config.has_section(os_profile_section):
        _print_warn(f"ATTENTION: OS profile section '[{os_profile_section}]' not found in global config. Using 'generic'.")
        os_profile_section = "install_defaults_generic"
        if not g_config.has_section(os_profile_section):
             _print_error(f"ERROR: Fallback profile '[install_defaults_generic]' also not found in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED}")
             sys.exit(1)

    # 2. Determine final values (profile defaults overridden by args)
    smp = args.smp or g_config.get(os_profile_section, "smp", fallback="2")
    memory = args.mem or g_config.get(os_profile_section, "memory", fallback="2G")
    disk_size = args.size or g_config.get(os_profile_section, "disk_size", fallback="16G")
    bridge = args.bridge or g_config.get("network", "bridge", fallback="br0") # Default bridge if not in global
    pool_name = args.pool or "default"

    # 3. Generate MAC and UUID
    mac = _generate_mac()
    vm_uuid = str(uuid.uuid4())

    # 4. Prepare the new configuration object
    new_config = configparser.ConfigParser()
    new_config["hardware"] = {
        "smp": smp,
        "memory": memory,
        "firmware": g_config.get("hardware", "firmware", fallback="uefi"),
        "chipset": g_config.get("hardware", "chipset", fallback="q35"),
        "uuid": vm_uuid,
        "os_type": args.os_type
    }
    new_config["network"] = {
        "bridge": bridge,
        "mac": mac
    }

    image_file_name = f"{vm_name}.qcow2" # Use VM name for disk image file
    disk_config = {
        "image_file": image_file_name,
        "image_format": g_config.get("disks", "image_format", fallback="qcow2")
    }
    # Only store pool name if it's not the default
    if pool_name != "default":
        disk_config["image_pool"] = pool_name

    new_config["disks"] = disk_config

    # Add placeholder [options] section
    new_config["options"] = {
        "; Example: Add custom QEMU flags below (uncomment the line)": "", # Use empty string
        "; extra_flags": "-vga virtio -display gtk,gl=on"
    }


    print(f"--- Creating New VM: {COLOR_BLUE}{vm_name}{COLOR_RESET} ---")
    print(f"  OS Type:     {args.os_type}")
    print(f"  Memory:      {memory}, SMP: {smp}")
    print(f"  Pool:        {pool_name}")
    print(f"  Disk:        {image_file_name} ({disk_size})")
    print(f"  Bridge:      {bridge}")
    print(f"  MAC:         {mac}")
    print(f"  UUID:        {vm_uuid}")

    # 5. Write the .conf file
    try:
        with open(conf_file, 'w') as f:
            f.write(f"; VM '{vm_name}' generated by vm_manager.py\n")
            # Use space_around_delimiters=False to avoid '=' after comments
            new_config.write(f, space_around_delimiters=False)
        _print_info(f"Configuration file saved: {COLOR_BLUE}{conf_file}{COLOR_RESET}")
    except Exception as e:
        _print_error(f"ERROR: Failed to save configuration file: {e}")
        sys.exit(1)

    # 6. Create the disk image
    try:
        # Resolve pool path from global config
        try:
            pool_path_str = g_config.get("pools", pool_name)
        except (configparser.NoSectionError, configparser.NoOptionError):
            _print_error(f"ERROR: Pool '{COLOR_YELLOW}{pool_name}{COLOR_RED}' not defined in {COLOR_BLUE}{GLOBAL_CONF}{COLOR_RED} [pools]")
            raise # Re-raise to trigger cleanup

        image_format = new_config.get("disks", "image_format")
        pool_path = Path(pool_path_str)
        # Ensure the target pool directory exists
        pool_path.mkdir(parents=True, exist_ok=True)
        image_path = pool_path / image_file_name

        _print_info(f"Creating disk at: {COLOR_BLUE}{image_path}{COLOR_RESET} (Size: {disk_size})...")
        # Run qemu-img create
        img_create_cmd = ["qemu-img", "create", "-f", image_format, str(image_path), disk_size]
        result = subprocess.run(img_create_cmd, check=False, capture_output=True, text=True)
        if result.returncode != 0:
             _print_error(f"ERROR: qemu-img create failed (rc={result.returncode}):")
             _print_error(result.stderr or result.stdout)
             raise subprocess.CalledProcessError(result.returncode, img_create_cmd, result.stdout, result.stderr)

    except Exception as e:
        _print_error(f"ERROR: Failed to create disk image: {e}")
        _print_info(f"Cleaning up: removing {COLOR_BLUE}{conf_file}{COLOR_RESET}")
        conf_file.unlink() # Clean up the conf file if disk creation fails
        sys.exit(1)

    # 7. Start the installer (graphical mode - local display for create)
    
    _print_info("\nStarting installer in graphical mode (remote SPICE)...")
    _print_info("Select boot device from the UEFI/BIOS menu if needed.")
    _print_info("Close the QEMU window when installation is complete.")

    try:
        # Re-read the config to get the fully merged view (global + new file)
        merged_config = get_vm_config(vm_name)
        
        qemu_cmd, spice_port, spice_password = _build_qemu_command(
            vm_name, merged_config, iso_list=args.iso, graphical_mode=True
        )
    except SystemExit:
         return # Error handled previously
    except Exception as e:
        _print_error(f"ERROR: building QEMU command for installer: {e}")
        sys.exit(1)

    if spice_port:
        _print_info(f"SPICE server configured on port: {COLOR_YELLOW}{spice_port}{COLOR_RESET}")
        hostname = socket.gethostname()
        print(f"Connect using a SPICE client (e.g., remote-viewer spice://{hostname}:{spice_port})")
    else:
        _print_warn("ATTENTION: SPICE was not configured. Installer may not be accessible.")

    # print(f"Command: {' '.join(qemu_cmd)}") # Debug
    try:
        subprocess.run(qemu_cmd, check=True)
    except subprocess.CalledProcessError as e:
        _print_error(f"ERROR: QEMU installer process failed: {e}")
    # --- INÍCIO DA CORREÇÃO (Ctrl+C) ---
    except KeyboardInterrupt:
        _print_warn(f"\nInstallation for '{COLOR_BLUE}{vm_name}{COLOR_RESET}' interrupted by user (Ctrl+C).")
        # O QEMU já recebeu o sinal e está terminando, então apenas saímos
    # --- FIM DA CORREÇÃO ---
    except Exception as e:
        _print_error(f"ERROR: QEMU installer process failed: {e}")


    _print_info(f"Installation for '{COLOR_BLUE}{vm_name}{COLOR_RESET}' finished.")


def send_monitor_command(sock_file: Path, command: str) -> bool:
    """Sends a command to the QEMU monitor socket."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(2) # Prevent hanging indefinitely
            s.connect(str(sock_file))
            s.sendall(command.encode('utf-8'))
            # Try to read a response to ensure command was likely processed
            # QEMU monitor usually sends back an empty prompt or confirmation
            s.recv(1024)
            return True
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout) as e:
        _print_error(f"ERROR: Could not connect to VM monitor socket ({COLOR_BLUE}{sock_file}{COLOR_RED}): {e}")
        return False
    except Exception as e:
        _print_error(f"ERROR: sending monitor command '{command.strip()}': {e}")
        return False


def handle_stop(args):
    """Sends a shutdown command (powerdown or quit) to the VM."""
    vm_name = args.vm_name
    pid_file, sock_file = get_vm_paths(vm_name)

    if not is_vm_running(pid_file):
        _print_error(f"ERROR: VM '{COLOR_BLUE}{vm_name}{COLOR_RED}' is not running.")
        # Clean up stale files if they exist
        if pid_file.exists():
            _print_warn(f"ATTENTION: Removing stale PID file: {COLOR_BLUE}{pid_file}{COLOR_YELLOW}")
            pid_file.unlink()
        if sock_file.exists():
            _print_warn(f"ATTENTION: Removing stale monitor socket: {COLOR_BLUE}{sock_file}{COLOR_YELLOW}")
            sock_file.unlink()
        sys.exit(1)

    # Shutdown logic: forced or graceful
    if args.force:
        _print_warn(f"ATTENTION: Forcing 'quit' (immediate shutdown) for '{COLOR_BLUE}{vm_name}{COLOR_YELLOW}'...")
        if not send_monitor_command(sock_file, "quit\n"):
            _print_error("Failed to send 'quit' command.")
            sys.exit(1)
    else:
        _print_info(f"Attempting ACPI shutdown (powerdown) for '{COLOR_BLUE}{vm_name}{COLOR_RESET}'...")
        if not send_monitor_command(sock_file, "system_powerdown\n"):
             _print_warn("ATTENTION: Failed to send powerdown command, trying 'quit'...")
             if not send_monitor_command(sock_file, "quit\n"):
                   _print_error("Failed to send 'quit' command after powerdown failure.")
                   sys.exit(1)
        else:
             # Wait up to 15 seconds for graceful shutdown
            _print_info("Waiting up to 15 seconds for VM to shut down...")
            shutdown_success = False
            for i in range(15):
                if not is_vm_running(pid_file):
                    _print_info("VM shut down successfully (powerdown).")
                    shutdown_success = True
                    break # Exit loop
                time.sleep(1)

            if not shutdown_success:
                # If still running after 15s, force quit
                _print_warn("ATTENTION: VM did not respond to powerdown. Forcing 'quit'...")
                if not send_monitor_command(sock_file, "quit\n"):
                    _print_error("Failed to send 'quit' command.")
                    sys.exit(1)

    # Final check block (for forced quit or fallback)
    _print_info("Waiting for QEMU process to terminate...")
    terminated = False
    for _ in range(5): # Wait 5 seconds for 'quit'
        if not is_vm_running(pid_file):
            _print_info("VM terminated.")
            terminated = True
            break # Exit loop
        time.sleep(1)

    # Final cleanup of socket file
    if sock_file.exists():
        try:
            sock_file.unlink()
        except OSError as e:
             _print_warn(f"ATTENTION: Could not remove monitor socket {sock_file}: {e}")

    if not terminated:
        try:
             pid_val = pid_file.read_text().strip()
             _print_error(f"ERROR: VM may still be running. Check PID: {pid_val}")
        except FileNotFoundError:
             _print_error(f"ERROR: VM did not terminate and PID file is missing.")


def handle_remove(args):
    """Stops, then removes the .conf, disk image, and _VARS.fd files for a VM."""
    vm_name = args.vm_name
    conf_file = VMS_DIR / f"{vm_name}.conf"
    uefi_vars_file = VMS_DIR / f"{vm_name}_VARS.fd"
    pid_file, _ = get_vm_paths(vm_name)

    # 1. Try to read the config. If it doesn't exist, handle potential stray files.
    try:
        config = get_vm_config(vm_name)
    except SystemExit:
        # If .conf is missing, check for other stray files
        _print_warn(f"ATTENTION: Config file for '{COLOR_BLUE}{vm_name}{COLOR_YELLOW}' not found.")
        stray_files = [uefi_vars_file] # Can't resolve disk without config
        existing_stray = [f for f in stray_files if f.exists()]

        if not existing_stray:
            _print_info("No configuration or related files found. Nothing to remove.")
            sys.exit(0)

        if not args.force:
             _print_warn("Checking for other related files...")
             for f in existing_stray:
                  print(f"  - {f}")
             try:
                 confirm = input("Stray files found. Remove them anyway? (yes/no): ")
             except EOFError:
                 print("\nCancelled.")
                 sys.exit(1)
             if confirm.lower() != 'yes':
                 print("Removal cancelled.")
                 sys.exit(0)

        # Proceed to delete stray files if forced or confirmed
        _print_info("Removing stray files...")
        all_removed = True
        for f in existing_stray:
             try:
                 f.unlink()
                 _print_info(f"Removed: {COLOR_BLUE}{f}{COLOR_RESET}")
             except Exception as e:
                 _print_error(f"ERROR: removing {f}: {e}")
                 all_removed = False
        sys.exit(0 if all_removed else 1)

    # 2. Stop the VM if running
    if is_vm_running(pid_file):
        _print_warn(f"ATTENTION: VM '{COLOR_BLUE}{vm_name}{COLOR_YELLOW}' is running. Forcing shutdown...")
        # Simulate args for 'handle_stop' with force=True
        stop_args = argparse.Namespace(vm_name=vm_name, force=True)
        handle_stop(stop_args)
        print("---") # Separator after stop output

    # 3. Find all associated files (now that config exists)
    try:
        disk_file_str, _ = resolve_image_path(config)
        disk_file = Path(disk_file_str)
    except SystemExit:
         # Error already printed by resolve_image_path
         _print_error("Cannot continue without disk path. Removal failed.")
         sys.exit(1)
    except Exception as e:
        _print_error(f"ERROR: resolving disk path: {e}")
        _print_error("Cannot continue without disk path. Removal failed.")
        sys.exit(1)

    # 4. Confirm with user
    files_to_delete = [conf_file, disk_file, uefi_vars_file]
    # Filter list to only include files that actually exist
    existing_files_to_delete = [f for f in files_to_delete if f.exists()]

    if not existing_files_to_delete:
         _print_info(f"No files associated with VM '{vm_name}' found. Nothing to remove.")
         sys.exit(0)

    if not args.force:
        _print_warn(f"ATTENTION: You are about to permanently remove '{COLOR_BLUE}{vm_name}{COLOR_YELLOW}'.")
        print("The following files will be deleted:")
        for f in existing_files_to_delete:
            print(f"  - {f}")

        try:
            confirm = input("Type 'yes' to confirm removal: ")
        except EOFError:
            print("\nCancelled.")
            sys.exit(1)

        if confirm.lower() != "yes":
            print("Removal cancelled.")
            sys.exit(0)

    # 5. Delete the files
    _print_info(f"Removing VM '{COLOR_BLUE}{vm_name}{COLOR_RESET}'...")
    all_removed = True
    for f in existing_files_to_delete:
        try:
            f.unlink()
            _print_info(f"Removed: {COLOR_BLUE}{f}{COLOR_RESET}")
        except Exception as e:
            _print_error(f"ERROR: removing file {f}: {e}")
            all_removed = False

    if all_removed:
        _print_info(f"VM '{COLOR_BLUE}{vm_name}{COLOR_RESET}' removed successfully.")
    else:
        _print_error("ERROR: Some files may not have been removed.")
        sys.exit(1)

# --- SERIALPTY FUNCTION REMOVED ---

def main():
    # Ensure base directories and default config exist
    VMS_DIR.mkdir(exist_ok=True)

    if not GLOBAL_CONF.exists():
        _print_warn(f"ATTENTION: Global configuration file '{COLOR_BLUE}{GLOBAL_CONF}{COLOR_YELLOW}' not found.")
        _print_info("Creating a default file...")
        try:
            with open(GLOBAL_CONF, 'w') as f:
                # Use strip() to remove leading/trailing whitespace from the multi-line string
                f.write(DEFAULT_GLOBAL_CONF.strip())
            _print_info(f"File '{COLOR_BLUE}{GLOBAL_CONF}{COLOR_RESET}' created successfully.")
            _print_warn(f"\n{COLOR_YELLOW}!!! ATTENTION: Please edit '{GLOBAL_CONF}' to adjust paths (bridge, firmware_paths, pools) !!!{COLOR_RESET}\n")

        except Exception as e:
            _print_error(f"ERROR: Failed to create '{GLOBAL_CONF}': {e}")
            sys.exit(1)

    # --- Argument Parser Setup ---
    parser = argparse.ArgumentParser(
        description="Simple QEMU VM Manager",
        formatter_class=argparse.RawDescriptionHelpFormatter # Preserves formatting in help text
    )
    # Use metavar to make command list cleaner in help
    subparsers = parser.add_subparsers(dest="command", required=True, metavar='COMMAND')

    # 'list' command
    list_parser = subparsers.add_parser("list", help="List all VMs or details for a specific VM.")
    list_parser.add_argument("vm_name", nargs="?", default=None, help="Optional VM name to show details.")
    list_parser.set_defaults(func=handle_list)

    # 'status' command
    status_parser = subparsers.add_parser("status", help="Check the status of a specific VM.")
    status_parser.add_argument("vm_name", metavar='VM_NAME', help="Name of the VM (e.g., windows10)")
    status_parser.set_defaults(func=handle_status)

    # 'start' command
    start_parser = subparsers.add_parser("start", help="Start an existing VM (headless or graphical/SPICE).")
    start_parser.add_argument("vm_name", metavar='VM_NAME', help="Name of the VM (e.g., windows10)")
    start_parser.add_argument(
        "--iso",
        action="append", # Can be specified multiple times
        metavar='ISO_PATH',
        help="Optional path to an ISO file (for maintenance mode)."
    )
    start_parser.add_argument(
        "--vga",
        action="store_true", # Becomes True if flag is present
        help="Start in SPICE mode (foreground) for remote graphical maintenance."
    )
    start_parser.add_argument(
        "--spice-port",
        type=int,
        metavar='PORT',
        help=f"Specify a SPICE port (range: {SPICE_PORT_MIN}-{SPICE_PORT_MAX}) for --vga mode. Random if omitted."
    )
    start_parser.set_defaults(func=handle_start)

    # 'create' command
    create_parser = subparsers.add_parser("create", help="Create a new VM and start the graphical installer.")
    create_parser.add_argument("vm_name", metavar='VM_NAME', help="Name for the new VM (e.g., windows11)")
    create_parser.add_argument(
        "--iso",
        required=True,
        action="append", # Can specify multiple ISOs
        metavar='ISO_PATH',
        help="Path to an ISO file. Use multiple times (e.g., --iso win.iso --iso drivers.iso)"
    )
    create_parser.add_argument("--os-type", choices=['windows', 'linux', 'generic'], default='generic', help="OS type for applying defaults (default: generic)")
    create_parser.add_argument("--smp", metavar='CORES', help="Override default SMP cores (e.g., 8)")
    create_parser.add_argument("--mem", metavar='MEMORY', help="Override default Memory (e.g., 8G)")
    create_parser.add_argument("--size", metavar='DISK_SIZE', help="Override default Disk size (e.g., 100G)")
    create_parser.add_argument("--bridge", metavar='BRIDGE_NAME', help="Override default Bridge (e.g., br_tap114)")
    create_parser.add_argument("--pool", metavar='POOL_NAME', help="Name of the storage pool to use (default: 'default')")
    create_parser.set_defaults(func=handle_create)

    # 'stop' command
    stop_parser = subparsers.add_parser("stop", help="Shut down a running VM (uses ACPI powerdown).")
    stop_parser.add_argument("vm_name", metavar='VM_NAME', help="Name of the VM (e.g., windows10)")
    stop_parser.add_argument("--force", action="store_true", help="Force shutdown (hard 'quit') without trying powerdown.")
    stop_parser.set_defaults(func=handle_stop)

    # 'remove' command
    remove_parser = subparsers.add_parser("remove", help="Remove a VM (disk, config, vars). Stops if running.")
    remove_parser.add_argument("vm_name", metavar='VM_NAME', help="Name of the VM to remove.")
    remove_parser.add_argument("--force", action="store_true", help="Skip removal confirmation.")
    remove_parser.set_defaults(func=handle_remove)

    # --- SERIALPTY PARSER REMOVED ---

    # Parse arguments and call the relevant function
    try:
        args = parser.parse_args()
        # Ensure script is run as root (needed for bridge, etc.)
        if os.geteuid() != 0:
             _print_error("ERROR: This script must be run as root (or using sudo).")
             sys.exit(1)

        args.func(args)
    except Exception as e:
        _print_error(f"ERROR: An unexpected error occurred: {e}")
        # Consider adding more detailed error handling or logging here if needed
        # import traceback
        # traceback.print_exc() # For debugging unhandled exceptions
        sys.exit(1)


if __name__ == "__main__":
    main()