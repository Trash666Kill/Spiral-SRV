#!/bin/bash

# Close on any error
set -e

host() {
    # Configure NAT and forwarding (BR_TAP110)
    br_tap110() {
        # Masquerade Rules
        nft add rule inet firelux postrouting ip saddr 10.0.10.0/24 oifname "$LAN" masquerade

        # Forward Rules
        nft add rule inet firelux forward iifname "br_tap110" oifname "$LAN" accept
    }

    # Call
    br_tap110
}

# Main function to orchestrate the setup
main() {
    RULES="
    host
    "

    for RULE in $RULES
    do
        $RULE
        sleep 2
    done
}

# Execute main function
main