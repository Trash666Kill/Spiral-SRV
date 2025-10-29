#!/bin/bash

# Close on any error (Optional)
#set -e

NIC0=enp1s0

zabbix() {
    80() {
        # Filter Rules
        nft add rule inet firelux input ip daddr 172.30.100.133 tcp dport 80 drop
    }

    # Call
    80
}


# Main function to orchestrate the setup
main() {
    RULES="
    zabbix
    "

    for RULE in $RULES
    do
        $RULE
        sleep 4
    done
}

# Execute main function
#main