#!/bin/bash

# Close on any error
set -e

# Physical interfaces
physical() {
    nic0() {
        ip link set dev "$NIC0_ALT" up
    }

    # Call
    nic0
}

# Virtual interfaces
virtual() {
    br_tap110() {
        ip tuntap add tap110 mode tap
        ip link set dev tap110 up
        brctl addbr br_tap110
        brctl stp br_tap110 on
        brctl addif br_tap110 tap110
        ip link set dev br_tap110 up
        ip addr add 10.0.10.254/24 dev br_tap110
    }

    br_vlan966() {
        ip link add link "$NIC0_ALT" name vlan966 type vlan id 966
        ip link set dev vlan966 up
        brctl addbr br_vlan966
        brctl stp br_vlan966 on
        brctl addif br_vlan966 vlan966
        ip link set dev br_vlan966 up
    }

    br_vlan710() {
        ip link add link "$NIC0_ALT" name vlan710 type vlan id 710
        ip link set dev vlan710 up
        brctl addbr br_vlan710
        ip link set dev br_vlan710 address
        brctl stp br_vlan710 on
        brctl addif br_vlan710 vlan710
        ip link set dev br_vlan710 up
        # NIC0_CONFIG
        ifconfig "$NIC0_ALT" 0.0.0.0 netmask 0.0.0.0
        # NIC0_DEFAULT_ROUTE
        ip route add default via 0.0.0.0 dev "$NIC0_ALT"
    }

    # Call
    br_tap110
    br_vlan966
    br_vlan710
}

# Main function to orchestrate the setup
main() {
    physical
    virtual
}

# Execute main function
main