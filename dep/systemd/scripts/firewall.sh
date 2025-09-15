#!/bin/bash

# Close on any error
set -e

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

ct602294_3390() {
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 3390 dnat to 10.0.10.240:3389

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 3390 accept
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
    #ct602294_3390
}

# Execute main function
main