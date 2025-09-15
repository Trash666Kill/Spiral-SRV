#!/bin/bash

#WAN0=enp1s0

# Close on any error (Optional)
#set -e

vm859224() {
    3390() {
        #RDP- MSTSC
        # DNAT Rules
        nft add rule inet firelux prerouting iifname "$WAN0" ip protocol tcp tcp dport 80 dnat to 10.0.11.3:80

        # Forward Rules
        nft add rule inet firelux forward ip protocol tcp tcp dport 80 accept
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