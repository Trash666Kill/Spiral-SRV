#!/bin/bash

# Close on any error
set -e

swap() {
    readonly DEVICE_SWAP_UUID=""

    echo "INFO: Validating script configuration..."
    if [[ -z "$DEVICE_SWAP_UUID" ]]; then
        echo "ERROR: The DEVICE_SWAP_UUID variable is not set." >&2
        exit 1
    fi

    echo "INFO: Configuring ZRAM..."

    modprobe zram 2>/dev/null

    local ZRAM_SIZE
    ZRAM_SIZE="$(($(grep -Po 'MemTotal:\s*\K\d+' /proc/meminfo)/2))KiB"

    local ZRAM_DEV
    ZRAM_DEV=$(zramctl --find --algorithm zstd --size "$ZRAM_SIZE")

    if [[ -n "$ZRAM_DEV" ]]; then
        echo "INFO: ZRAM device created at $ZRAM_DEV with size $ZRAM_SIZE"

        mkswap -U clear "$ZRAM_DEV" >/dev/null
        swapon --discard --priority 100 "$ZRAM_DEV"

        if [[ $? -eq 0 ]]; then
            echo "SUCCESS: ZRAM active on $ZRAM_DEV (Priority 100)."
        else
            echo "ERROR: Failed to activate swapon on $ZRAM_DEV." >&2
        fi
    else
        echo "ERROR: Could not create/find a ZRAM device with zramctl." >&2
    fi

    echo

    echo "INFO: Activating disk swap for hibernation support..."

    swapoff -U "${DEVICE_SWAP_UUID}" 2>/dev/null

    swapon --priority -10 -U "${DEVICE_SWAP_UUID}"

    if [[ $? -eq 0 ]]; then
        echo "SUCCESS: Disk Swap activated (Priority -10)."
    else
        echo "ERROR: Failed to activate Disk Swap. Check UUID." >&2
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