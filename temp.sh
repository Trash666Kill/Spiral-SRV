#!/bin/bash

# Close on any error
set -e

swap() {
    # Insert the UUID of your swap partition here
    readonly DEVICE_SWAP_UUID="d3affb2c-bdba-4786-b8ed-d02b8d93d371"

    echo "INFO: Validating script configuration..."
    if [[ -z "$DEVICE_SWAP_UUID" ]]; then
        echo "ERROR: The DEVICE_SWAP_UUID variable is not set." >&2
        exit 1
    fi

    # ZRAM Configuration (Primary Swap - Priority 100)
    echo "INFO: Configuring ZRAM..."
    modprobe zram 2>/dev/null

    # Calculate size (50% of Total RAM)
    local ZRAM_SIZE
    ZRAM_SIZE="$(($(grep -Po 'MemTotal:\s*\K\d+' /proc/meminfo)/2))KiB"

    # Find a free ZRAM device and configure it
    # Using --find ensures the device node (e.g., /dev/zram0) is created dynamically
    local ZRAM_DEV
    ZRAM_DEV=$(zramctl --find --algorithm zstd --size "$ZRAM_SIZE")

    if [[ -n "$ZRAM_DEV" ]]; then
        # Format and activate
        mkswap -U clear "$ZRAM_DEV" >/dev/null 2>&1
        swapon --discard --priority 100 "$ZRAM_DEV" 2>/dev/null
        
        # Verify activation
        if swapon --show | grep -q "$ZRAM_DEV"; then
             echo "SUCCESS: ZRAM active on $ZRAM_DEV (Priority 100)."
        else
             echo "ERROR: Could not verify ZRAM activation." >&2
        fi
    else
        echo "ERROR: Could not create/find a ZRAM device." >&2
    fi

    echo

    # Disk Swap Configuration (Hibernation/Fallback - Priority -2)
    echo "INFO: Configuring disk swap priority to -2..."

    # Resolve UUID to actual device path (e.g., /dev/nvme0n1p3) for reliability
    local DISK_DEV
    DISK_DEV=$(blkid -U "$DEVICE_SWAP_UUID")

    if [[ -n "$DISK_DEV" ]]; then
        echo "INFO: Found swap device at $DISK_DEV"
        
        # Turn off first to ensure we can re-apply the specific priority
        swapoff "$DISK_DEV" 2>/dev/null
        
        # Activate with priority -2 as requested
        swapon --priority -2 "$DISK_DEV"
        
        if [[ $? -eq 0 ]]; then
            echo "SUCCESS: Disk Swap activated on $DISK_DEV (Priority -2)."
        else
            echo "WARNING: Failed to set explicit priority -2." >&2
            echo "INFO: Retrying without explicit priority (System will auto-assign negative)."
            # Fallback: Let the kernel decide the negative priority
            swapon "$DISK_DEV"
        fi
    else
        echo "ERROR: Could not find device with UUID: $DEVICE_SWAP_UUID" >&2
    fi

    echo "INFO: Current swap status:"
    swapon --show
}

container_a() {
    local USE_LUKS=${USE_LUKS:-}  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID="8249eb58-8580-4665-8b59-b12219950a73"  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/Container/A"
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

container_b() {
    local USE_LUKS=${USE_LUKS:-}  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID="053acf9c-d9b5-4d7a-b822-52ae9abaf49b"  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/Container/B"
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

container_c() {
    local USE_LUKS=${USE_LUKS:-}  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID="4c5e7c6e-5705-4c2a-9d51-a06072acd034"  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/Container/C"
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

usb_a() {
    local USE_LUKS=yes
    local DEVICE_UUID="407c2263-1f73-4424-a56b-2e1cb87a15af"  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/USB/A"
    local OPTIONS=""
    local LUKS_DEVICE="/dev/disk/by-uuid/ab6727c7-8a1a-4db8-b4b8-2f2dffb4595c"
    local LUKS_NAME="USB-A_crypt"
    local LUKS_KEY_FILE="/root/.crypt/ab6727c7-8a1a-4db8-b4b8-2f2dffb4595c.key"

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

hs_stg_02_iso() {
    local NFS_SOURCE="172.30.100.197:/mnt/Local/Container/A/Miscellaneous/T.I/ISO"  # NFS share address
    local MOUNT_POINT="/mnt/Remote/Servers/hs-stg-02/Container/A/Miscellaneous/T.I/ISO"
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
    swap
    container_a
    container_b
    container_c
    usb_a
    set +e
    hs_stg_02_iso 2> /dev/null
}

# Execute main function
main