#!/bin/bash

# Close on any error (Optional)
#set -e

NIC0=br_vlan710

vm859224() {
    3390() {
        #RDP- MSTSC
        # DNAT Rules
        nft add rule inet firelux prerouting iifname "$NIC0" ip protocol tcp tcp dport 3390 dnat to 10.0.10.2:3389

        # Forward Rules
        nft add rule inet firelux forward ip protocol tcp tcp dport 3390 accept
    }

    # Call
    3390
}

# Main function to orchestrate the setup
main() {
    RULES="
    vm859224
    "

    for RULE in $RULES
    do
        $RULE
        sleep 4
    done
}

# Execute main function
#main