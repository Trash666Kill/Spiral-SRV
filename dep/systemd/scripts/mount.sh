#!/bin/bash

# Close on any error
set -e

swap() {
    readonly DEVICE_SWAP_UUID=""

    readonly CALCULATION_BASE_MB=512
    readonly THRESHOLD_MB=512

    echo "INFO: Validating script configuration..."
    if [[ -z "$DEVICE_SWAP_UUID" ]]; then
        echo "ERROR: The DEVICE_SWAP_UUID variable is not set in the script." >&2
        echo "Please edit the script and provide a valid UUID in the CONFIGURATION section." >&2
        exit 1
    fi
    echo "INFO: Configuration is valid. Proceeding with execution."

    TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))

    echo "INFO: Detected Total System RAM: ${TOTAL_MEM_MB} MB"

    if (( TOTAL_MEM_MB > THRESHOLD_MB )); then
        echo "INFO: System has more than ${THRESHOLD_MB} MB of RAM. Calculating dynamic swappiness value."
        CALCULATED_SWAPPINESS=$(awk -v base="${CALCULATION_BASE_MB}" -v total="${TOTAL_MEM_MB}" 'BEGIN {printf "%.0f", (base / total) * 100}')

        echo "INFO: Calculated swappiness value: ${CALCULATED_SWAPPINESS}"
        sysctl vm.swappiness=${CALCULATED_SWAPPINESS}

        if [[ $? -ne 0 ]]; then
            echo "ERROR: Failed to set vm.swappiness." >&2
        else
            CURRENT_SWAPPINESS=$(sysctl -n vm.swappiness)
            echo "SUCCESS: vm.swappiness is now set to ${CURRENT_SWAPPINESS}."
        fi
    else
        echo "INFO: System has ${THRESHOLD_MB} MB of RAM or less."
        echo "INFO: Skipping swappiness configuration to rely on system defaults."
    fi

    echo

    echo "INFO: Activating swap device with specified UUID: ${DEVICE_SWAP_UUID}"
    swapon -U "${DEVICE_SWAP_UUID}"

    if [[ $? -eq 0 ]]; then
        echo "SUCCESS: Swap activation command finished."
    else
        echo "ERROR: The swap activation command failed. Please verify the UUID is correct." >&2
    fi

    echo "INFO: Current swap status:"
    swapon --show
}

mount_unit() {
    local USE_LUKS=${USE_LUKS:-}  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID="407c2263-1f73-4424-a56b-2e1cb87a15af"  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/hsugisawa"
    local OPTIONS=""
    local LUKS_DEVICE="/dev/disk/by-uuid/898221be-388d-4b20-bfdc-74759afb8dce"
    local LUKS_NAME="Container-A_crypt"
    local LUKS_KEY_FILE="/root/.crypt/898221be-388d-4b20-bfdc-74759afb8dce.key"

    # Handle LUKS decryption if enabled
    if [ "$USE_LUKS" = "yes" ]; then
        if [ ! -e "/dev/mapper/$LUKS_NAME" ]; then
            cryptsetup luksOpen "$LUKS_DEVICE" "$LUKS_NAME" --key-file "$LUKS_KEY_FILE"
        fi
        DEVICE_UUID="/dev/mapper/$LUKS_NAME"
    fi

    # Create mount point if it doesn't exist
    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT"

    # Perform the mount
    if [ "$USE_LUKS" = "yes" ]; then
        if [ -n "$OPTIONS" ]; then
            mount "$DEVICE_UUID" "$MOUNT_POINT" -o "$OPTIONS"
        else
            mount "$DEVICE_UUID" "$MOUNT_POINT"
        fi
    else
        if [ -n "$OPTIONS" ]; then
            mount -U "$DEVICE_UUID" "$MOUNT_POINT" -o "$OPTIONS"
        else
            mount -U "$DEVICE_UUID" "$MOUNT_POINT"
        fi
    fi
}

nfs_mount_unit() {
    local NFS_SOURCE="172.30.100.22:/hsugisawa"  # NFS share address
    local MOUNT_POINT="/mnt/hsugisawa_nfs"
    local OPTIONS=""

    # Create mount point if it doesn't exist
    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT"

    # Perform the mount
    if [ -n "$OPTIONS" ]; then
        mount -t nfs "$NFS_SOURCE" "$MOUNT_POINT" -o "$OPTIONS"
    else
        mount -t nfs "$NFS_SOURCE" "$MOUNT_POINT"
    fi
}

smb_mount_unit() {
    local SMB_SOURCE="//172.30.100.22/hsugisawa"  # SMB share address
    local MOUNT_POINT="/mnt/hsugisawa_smb"
    local OPTIONS=""  # Exemplo: "username=USER,password=PASS" ou "credentials=/root/.smbcredentials"

    # Create mount point if it doesn't exist
    [ -d "$MOUNT_POINT" ] || mkdir -p "$MOUNT_POINT"

    # Perform the mount
    if [ -n "$OPTIONS" ]; then
        mount -t cifs "$SMB_SOURCE" "$MOUNT_POINT" -o "$OPTIONS"
    else
        mount -t cifs "$SMB_SOURCE" "$MOUNT_POINT"
    fi
}

# Main function to orchestrate the setup
main() {
    #swap
    mount_unit
    nfs_mount_unit
    smb_mount_unit
}

# Execute main function
#main