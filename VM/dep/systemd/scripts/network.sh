#!/bin/bash

# Close on any error
set -e

# Physical interfaces
interfaces() {
    nic0() {
        dhclient -r "$NIC0_ALT"
        dhclient -v "$NIC0_ALT"
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