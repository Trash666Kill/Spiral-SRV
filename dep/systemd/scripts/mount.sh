#!/bin/bash

# Close on any error
set -e

swap() {
    local DEVICE_UUID=""

    swapon -U "$DEVICE_UUID"
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