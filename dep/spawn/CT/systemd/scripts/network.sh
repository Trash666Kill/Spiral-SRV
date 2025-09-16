#!/bin/bash

# Close on any error
set -e

# Physical interfaces
interfaces() {
    nic0() {
        NIC0=eth0
        dhclient -r "$NIC0"
        dhclient -v "$NIC0"
    }

    nic1() {
        NIC1=eth1
        ifconfig "$NIC1" 172.30.100.133/24
        ip route add default via 172.30.100.60 dev "$NIC1"
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