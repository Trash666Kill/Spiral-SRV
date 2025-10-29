#!/bin/bash

# Close on any error
set -e

NIC0=ens2

# Physical interfaces
interfaces() {
    nic0() {
        dhcpcd --rebind "$NIC0"
    }

    nic1() {
        ifconfig "$NIC1_ALT" 172.30.100.133/24
        ip route add default via 172.30.100.60 dev "$NIC1_ALT"
    }

    # Call
    nic0
    #nic1
}

# Main function to orchestrate the setup
main() {
    interfaces
}

# Execute main function
main