#!/bin/bash

# - Description: Configures nftables firewall for virtual machines.
# - Enables IP forwarding, restarts nftables, and sets up tables, chains, and rules
# for filtering, NAT, and connection tracking (e.g., established connections).
# - Includes optional rules (e.g., zabbix). Exits on any error using set -e.
# - To add new rules or configurations, copy and edit functions like chains or zabbix.

# Close on any error
set -e

# Interfaces
#WAN=''

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
    nft add chain inet firelux input { type filter hook input priority filter \; policy accept \; }
    nft add chain inet firelux forward { type filter hook forward priority filter \; policy drop \; }
    nft add chain inet firelux prerouting { type nat hook prerouting priority 0 \; policy accept \; }
    nft add chain inet firelux postrouting { type nat hook postrouting priority srcnat \; policy accept \; }
}

# Allow established and related connections
established_related() {
    # Filter Rules
    nft add rule inet firelux forward ct state established,related accept
}

zabbix() {
    # Filter Rules
    nft add rule inet firelux input ip daddr 172.30.100.133 tcp dport 80 drop
}

# Main function to orchestrate the setup
main() {
    ip_forwarding
    restart_nftables
    flush_nftables
    main_table
    chains
    established_related
    #zabbix
}

# Execute main function
main