#!/bin/bash

# Close on any error
set -e

swap() {
    # Insert the UUID of your swap partition here
    readonly DEVICE_SWAP_UUID=""

    local TARGET_MODE="${1:-both}" # Default to 'both'

    echo "INFO: Validating script configuration for mode: $TARGET_MODE..."

    # 1. Validation: Fail only if UUID is missing AND mode requires disk
    if [[ -z "$DEVICE_SWAP_UUID" ]]; then
        if [[ "$TARGET_MODE" =~ ^(swap|both)$ ]]; then
            echo "ERROR: The DEVICE_SWAP_UUID variable is not set (Required for mode '$TARGET_MODE')." >&2
            return 1
        else
            echo "WARNING: DEVICE_SWAP_UUID not set. Disk swap management will be skipped."
        fi
    fi

    # Allow 'zram' as an alias for 'zwap'
    if [[ ! "$TARGET_MODE" =~ ^(swap|zwap|zram|both)$ ]]; then
        echo "ERROR: Invalid mode. Use: 'swap', 'zwap' (or 'zram'), or 'both'." >&2
        return 1
    fi

    # ZRAM Configuration (Active for 'zwap', 'zram' and 'both')
    if [[ "$TARGET_MODE" =~ ^(zwap|zram|both)$ ]]; then
        # Check if already active to avoid duplicates
        if swapon --show | grep -q "/dev/zram"; then
             echo "INFO: ZRAM is already active. Skipping creation."
        else
             echo "INFO: Configuring ZRAM..."
             modprobe zram 2>/dev/null || true

             # Calculate size (50% of Total RAM)
             local ZRAM_SIZE
             ZRAM_SIZE="$(($(grep -Po 'MemTotal:\s*\K\d+' /proc/meminfo)/2))KiB"

             # Find a free ZRAM device and configure it
             local ZRAM_DEV
             ZRAM_DEV=$(zramctl --find --algorithm zstd --size "$ZRAM_SIZE")

             if [[ -n "$ZRAM_DEV" ]]; then
                 # Format and activate
                 mkswap -U clear "$ZRAM_DEV" >/dev/null 2>&1
                 swapon --discard --priority 100 "$ZRAM_DEV" 2>/dev/null || true
                 
                 echo "SUCCESS: ZRAM active on $ZRAM_DEV (Priority 100)."
             else
                 echo "ERROR: Could not create/find a ZRAM device." >&2
             fi
        fi
    else
        # If mode is 'swap' (disk only), ensure ZRAM is OFF
        if swapon --show | grep -q "/dev/zram"; then
            echo "INFO: Mode is '$TARGET_MODE'. Disabling ZRAM..."
            swapoff /dev/zram* 2>/dev/null || true
            zramctl --reset-all 2>/dev/null || true
        fi
    fi

    echo

    # Disk Swap Configuration (Active for 'swap' and 'both')
    local DISK_DEV=""
    # Only try to resolve disk if UUID is set
    if [[ -n "$DEVICE_SWAP_UUID" ]]; then
        DISK_DEV=$(blkid -U "$DEVICE_SWAP_UUID")
    fi

    # If mode needs disk but device not found (and UUID was provided), fail.
    if [[ "$TARGET_MODE" =~ ^(swap|both)$ ]]; then
        if [[ -z "$DISK_DEV" ]]; then
             echo "ERROR: Could not find device with UUID: $DEVICE_SWAP_UUID" >&2
             return 1
        fi
        echo "INFO: Configuring disk swap ($TARGET_MODE)..."

        # Check if we need to reactivate to change priority
        if grep -q "$DISK_DEV" /proc/swaps; then
             echo "INFO: Disk swap active. Adjusting priority..."
             swapoff "$DISK_DEV" 2>/dev/null || true
        fi

        # Priority -2 ensures it is used after ZRAM (100)
        if swapon --priority -2 "$DISK_DEV" 2>/dev/null; then
            echo "SUCCESS: Disk Swap activated on $DISK_DEV (Priority -2)."
        else
            echo "WARNING: Failed to set explicit priority -2." >&2
            swapon "$DISK_DEV" || echo "ERROR: Could not activate swap." >&2
        fi

    elif [[ "$TARGET_MODE" =~ ^(zwap|zram)$ ]]; then
        # If mode is 'zwap' (zram only), ensure Disk is OFF (Only if we know the disk)
        if [[ -n "$DISK_DEV" ]]; then
            if grep -q "$DISK_DEV" /proc/swaps; then
                echo "INFO: Mode is '$TARGET_MODE'. Disabling Disk Swap..."
                swapoff "$DISK_DEV" 2>/dev/null || true
            fi
        fi
    fi

    echo "INFO: Current swap status:"
    swapon --show || true
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