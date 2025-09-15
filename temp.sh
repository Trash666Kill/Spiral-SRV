#!/bin/bash

# - Description: Starts a container using lxc and optionally restarts lxc.
# - Defines a function to restart the lxc service, which exits on failure.
# - Starts the CT named CT123456 using `lxc-start`.
# - The `main` function calls the CT start routine; restart_lxc is defined but unused.
# - To manage other CTs, duplicate and edit the CT123456 function accordingly.

# Restart lxc service
restart_lxc() {
    local SERVICE=lxc
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

CT212810() {
    # Music Streaming - Navidrome
    lxc-start --name CT212810
}

CT915942() {
    # Video Streaming - Jellyfin
    lxc-start --name CT915942
}

CT879677() {
    # Web P2P Client - Transmission
    lxc-start --name CT879677
}

CT442878() {
    # Music Streaming - MPD Server with USB DAC passthrough
    lxc-start --name CT442878
}

CT418656() {
    # AI Code Editor - Windsurf
    lxc-start --name CT418656
}

# Main function to orchestrate the setup
main() {
    restart_lxc

    containers="
    CT212810
    CT915942
    CT879677
    CT442878
    CT418656
    "

    for container in $containers
    do
        $container
        sleep 8
    done
}

# Execute main function
main

#!/bin/bash

# - Description: Starts a virtual machine using virsh and optionally restarts libvirtd.
# - Defines a function to restart the libvirtd service, which exits on failure.
# - Starts the VM named VM123456 using `virsh start`.
# - The `main` function calls the VM start routine; restart_libvirtd is defined but unused.
# - To manage other VMs, duplicate and edit the VM123456 function accordingly.

# Restart libvirtd service
restart_libvirtd() {
    local SERVICE=libvirtd
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

VM777095() {
    # Media Converter - Handbrake / noVNC
    virsh start VM777095
}

# Main function to orchestrate the setup
main() {
    restart_lxc

    vmachines="
    VM777095
    "

    for vmachine in $vmachines
    do
        $vmachine
        sleep 16
    done
}

# Execute main function
main

#!/bin/bash

# - Description: Mounts a device by UUID, an NFS share, or an SMB share to specified mount points.
# - Optionally decrypts a LUKS device before mounting for mount_unit if USE_LUKS is set to 'yes'.
# - Creates the mount point directories if they do not exist and mounts the device or share using provided UUID, source, and options.
# - Uses 'mount -U' for UUID-based mounts (non-LUKS in mount_unit) and 'mount' for NFS, SMB, or LUKS-mapped devices.
# - The nfs_mount_unit and smb_mount_unit functions are designed for network file systems and do not support LUKS decryption.
# - The OPTIONS variable is empty by default in all functions; specify custom mount options as needed.
# - Exits on any error using set -e.
# - To modify mount parameters, edit the DEVICE_UUID, NFS_SOURCE, SMB_SOURCE, MOUNT_POINT, or OPTIONS variables in the respective functions.
# - To enable LUKS decryption for mount_unit, set USE_LUKS="yes" before running the script.

# Close on any error
set -e

pool_a() {
    50026B7785D3588D() {
        local USE_LUKS="yes"  # Enable LUKS if USE_LUKS="yes"
        local DEVICE_UUID=""  # UUID for non-LUKS case
        local MOUNT_POINT="/mnt/Local/Container/A"
        local OPTIONS=""
        local LUKS_DEVICE="/dev/disk/by-uuid/ca52541c-b2e3-41e5-9c31-3048833fa08b"
        local LUKS_NAME="Container-A_crypt"
        local LUKS_KEY_FILE="/root/.crypt/ca52541c-b2e3-41e5-9c31-3048833fa08b.key"

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

    50026B7785D35B0D() {
        local USE_LUKS="yes"  # Enable LUKS if USE_LUKS="yes"
        local DEVICE_UUID=""  # UUID for non-LUKS case
        local MOUNT_POINT="/mnt/Local/Container/B"
        local OPTIONS=""
        local LUKS_DEVICE="/dev/disk/by-uuid/f515527f-eb65-424c-b88b-652e84609c5e"
        local LUKS_NAME="Container-B_crypt"
        local LUKS_KEY_FILE="/root/.crypt/f515527f-eb65-424c-b88b-652e84609c5e.key"

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

    # Call
    50026B7785D3588D
    50026B7785D35B0D
    mergerfs -o defaults,allow_other,category.create=mfs,minfreespace=8G /mnt/Local/Container/A:/mnt/Local/Container/B /mnt/Local/Pool/A
}

container_c() {
    local USE_LUKS="yes"  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID=""  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/Container/C"
    local OPTIONS=""
    local LUKS_DEVICE="/dev/disk/by-uuid/0842a224-fa02-4707-a6b3-d8ade5f4d2fd"
    local LUKS_NAME="Container-C_crypt"
    local LUKS_KEY_FILE="/root/.crypt/0842a224-fa02-4707-a6b3-d8ade5f4d2fd.key"

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

container_d() {
    local USE_LUKS="yes"  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID=""  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/Container/D"
    local OPTIONS=""
    local LUKS_DEVICE="/dev/disk/by-uuid/a58059c1-1254-4b8f-893d-220e8d9b6b6b"
    local LUKS_NAME="Container-D_crypt"
    local LUKS_KEY_FILE="/root/.crypt/a58059c1-1254-4b8f-893d-220e8d9b6b6b.key"

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

container_e() {
    local USE_LUKS="yes"  # Enable LUKS if USE_LUKS="yes"
    local DEVICE_UUID=""  # UUID for non-LUKS case
    local MOUNT_POINT="/mnt/Local/Container/E"
    local OPTIONS=""
    local LUKS_DEVICE="/dev/disk/by-uuid/bac62242-f1ff-43dc-bf18-cfedfbc1f1ff"
    local LUKS_NAME="Container-E_crypt"
    local LUKS_KEY_FILE="/root/.crypt/bac62242-f1ff-43dc-bf18-cfedfbc1f1ff.key"

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

# Main function to orchestrate the setup
main() {
    pool_a
    container_c
    container_d
    container_e
}

# Execute main function
main

#!/bin/bash

# - Description: Configures nftables firewall for workstations.
# - Enables IP forwarding, restarts nftables, and sets up tables, chains, and rules
# for filtering, NAT, and connection tracking (e.g., established connections).
# - Includes optional rules (e.g., zabbix). Exits on any error using set -e.
# - To add new rules or configurations, copy and edit functions like chains or zabbix.

# Close on any error
set -e

# Interfaces
WAN='enp4s0'

# Enable IP forwarding
ip_forwarding() {
    sysctl -w net.ipv4.ip_forward=1
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to enable IP forwarding.\n"
        exit 1
    fi
}

# Restart nftables service
restart_nftables() {
    local SERVICE=nftables
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

# Flush existing nftables rules
flush_nftables() {
    nft flush ruleset
}

# Create main table
main_table() {
    nft add table inet firelux
}

# Create chains
chains() {
    nft add chain inet firelux forward { type filter hook forward priority filter \; policy drop \; }
    nft add chain inet firelux prerouting { type nat hook prerouting priority 0 \; policy accept \; }
    nft add chain inet firelux postrouting { type nat hook postrouting priority srcnat \; policy accept \; }
}

# Allow established and related connections
established_related() {
    # Filter Rules
    nft add rule inet firelux forward ct state established,related accept
}

# Configure NAT and forwarding for Bridge (BR_TAP110)
br_tap110() {
    # Masquerade Rules
    nft add rule inet firelux postrouting ip saddr 10.0.10.0/24 oifname "$WAN" masquerade

    # Forward Rules
    nft add rule inet firelux forward iifname "br_tap110" oifname "$WAN" accept
}

ct212810_4533() {
    # Music Streaming - Navidrome
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 4533 dnat to 10.0.10.2:4533

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 4533 accept
}

ct915942_8096() {
    # Video Streaming - Jellyfin
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 8096 dnat to 10.0.10.3:8096

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 8096 accept
}

9091() {
    # Web P2P Client - Transmission
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 9091 dnat to 10.0.10.4:9091

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 9091 accept
}

ct442878() {
    # Music Streaming - MPD Server with USB DAC passthrough
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 5644 dnat to 10.0.10.5:5644
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 6600 dnat to 10.0.10.5:6600

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 5644 accept
    nft add rule inet firelux forward ip protocol tcp tcp dport 6600 accept
}

ct418656_6081() {
    # AI Code Editor - Windsurf
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 6081 dnat to 10.0.10.6:6080

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 6080 accept
}

# Main function to orchestrate the setup
main() {
    ip_forwarding
    restart_nftables
    flush_nftables
    main_table
    chains
    established_related
    br_tap110
    ct212810_4533
    ct915942_8096
    ct442878
    ct418656_6081
}

# Execute main function
main