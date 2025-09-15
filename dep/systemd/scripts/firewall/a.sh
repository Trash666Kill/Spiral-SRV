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
    echo "Restarting $SERVICE..."
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

# Flush existing nftables rules
flush_nftables() {
    echo "Flushing ruleset..."
    nft flush ruleset
}

# Create main table
main_table() {
    echo "Creating table..."
    nft add table inet firelux
}

# Create chains with default accept policy
chains() {
    echo "Creating chains..."
    nft add chain inet firelux forward { type filter hook forward priority filter \; policy drop \; }
    nft add chain inet firelux prerouting { type nat hook prerouting priority 0 \; policy accept \; }
    nft add chain inet firelux postrouting { type nat hook postrouting priority srcnat \; policy accept \; }
}

# Allow established and related connections (essential for stateful firewall)
established_related() {
    echo "Allowing established/related connections..."
    nft add rule inet firelux forward ct state established,related accept
}

# Setup logging for dropped packets
setup_logging() {
    echo "Setting up logging..."
    nft add rule inet firelux forward log prefix \"FORWARD_DROP: \" level info
}

# Main function to orchestrate the setup
main() {
    RULES="
    ip_forwarding
    restart_nftables
    flush_nftables
    main_table
    chains
    established_related
    setup_logging
    "

    for RULE in $RULES
    do
        $RULE
    done
}

# Execute main function
main