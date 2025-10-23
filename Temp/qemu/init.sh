#!/bin/bash
set -euo pipefail # Exit script if any command fails

# ==================================
#       QEMU VM Configuration
# ==================================
# Change the variables below as needed

# --- Hardware ---
VM_MEM="2G"      # RAM Memory
VM_SMP="2"       # Number of cores (vCPUs)
VM_CPU="host"    # CPU Model (host pass-through)

# --- Media / Disks ---

# Path to the installation .iso file
ISO_FILE="OS_ISO.iso" 

# Path to the VM's disk file (where the OS will be installed)
IMG_FILE="Image.img"
IMG_FORMAT="qcow2" # Disk file format

# --- Networking ---

# Name of the bridge interface on your HOST machine
# (e.g., br0, virbr0, etc.)
HOST_BRIDGE="br0"

# --- QEMU Binary ---
# The command to run QEMU
QEMU_BIN="qemu-system-x86_64"


# ==================================
#         VM Execution
# ==================================

printf "Verifying if image '%s' exists...\n" "$IMG_FILE"
if [ ! -f "$IMG_FILE" ]; then
    printf "------------------------------------------------------------------\n"
    printf "ERROR: Image file '%s' not found.\n" "$IMG_FILE"
    printf "You must create it before starting the VM.\n"
    printf "Example: qemu-img create -f %s %s 32G\n" "$IMG_FORMAT" "$IMG_FILE"
    printf "------------------------------------------------------------------\n"
    exit 1
fi

printf "Starting VM...\n"
printf "ISO: %s\n" "$ISO_FILE"
printf "Disk: %s\n" "$IMG_FILE"
printf "Bridge: %s\n" "$HOST_BRIDGE"
printf -- "---\n" # Use -- to ensure "---" is not treated as an option

# 'sudo' is required for the -netdev bridge option,
# as it needs privileged access to connect to the host bridge.
sudo "$QEMU_BIN" \
    -enable-kvm \
    -cpu "$VM_CPU" \
    -smp "$VM_SMP" \
    -m "$VM_MEM" \
    -boot menu=on \
    \
    -drive file="$IMG_FILE",if=virtio,format=$IMG_FORMAT,media=disk \
    \
    -device ahci,id=ahci0 \
    -drive file="$ISO_FILE",id=cdrom_sata,if=none,media=cdrom,readonly=on \
    -device ide-cd,bus=ahci0.0,drive=cdrom_sata \
    \
    -device virtio-net-pci,netdev=net0 \
    -netdev bridge,id=net0,br="$HOST_BRIDGE"

printf -- "---\n"
printf "VM shutdown.\n"