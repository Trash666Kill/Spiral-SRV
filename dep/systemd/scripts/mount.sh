#!/bin/bash

# Close on any error
set -e

swap() {
    local SWAP_UUID="$1"
    local VERBOSE_MODE="$2"
    local IS_VERBOSE=false

    if [[ "$VERBOSE_MODE" == "verbose" ]]; then
        IS_VERBOSE=true
    fi

    # Internal logging function
    _log() {
        if [[ "$IS_VERBOSE" == true ]]; then
            echo "INFO: $1"
        fi
    }

    # --- Constants for the logic ---
    local THRESHOLD_MB=512
    local CALCULATION_BASE_MB=512

    # --- Execution ---
    _log "Validating configuration..."
    if [[ -z "$SWAP_UUID" ]]; then
        echo "ERROR: No swap UUID was provided to the function." >&2
        return 1
    fi
    _log "Configuration is valid. Proceeding."

    local TOTAL_MEM_KB
    local TOTAL_MEM_MB
    TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
    TOTAL_MEM_MB=$((TOTAL_MEM_KB / 1024))
    _log "Detected Total System RAM: ${TOTAL_MEM_MB} MB"

    if (( TOTAL_MEM_MB > THRESHOLD_MB )); then
        _log "System has more than ${THRESHOLD_MB} MB of RAM. Calculating swappiness."
        local CALCULATED_SWAPPINESS
        CALCULATED_SWAPPINESS=$(awk -v base="${CALCULATION_BASE_MB}" -v total="${TOTAL_MEM_MB}" 'BEGIN {printf "%.0f", (base / total) * 100}')

        _log "Calculated swappiness value: ${CALCULATED_SWAPPINESS}"
        sysctl vm.swappiness=${CALCULATED_SWAPPINESS} >/dev/null

        if [[ $? -ne 0 ]]; then
            echo "ERROR: Failed to set vm.swappiness." >&2
            return 1
        fi
        _log "Successfully set vm.swappiness to $(sysctl -n vm.swappiness)."
    else
        _log "System has ${THRESHOLD_MB} MB of RAM or less. Skipping swappiness configuration."
    fi

    echo # Blank line for readability in verbose mode

    _log "Activating swap device with specified UUID: ${SWAP_UUID}"
    swapon -U "${SWAP_UUID}"

    if [[ $? -ne 0 ]]; then
        echo "ERROR: Swap activation failed. Please verify the UUID is correct: ${SWAP_UUID}" >&2
        return 1
    fi
    _log "Swap activation command finished successfully."

    if [[ "$IS_VERBOSE" == true ]]; then
        echo "INFO: Current swap status:"
        swapon --show
    fi

    return 0
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