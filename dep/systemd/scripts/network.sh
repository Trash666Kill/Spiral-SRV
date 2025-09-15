#!/bin/bash

# - Description: Configures network interfaces for workstation.
# - Sets up interfaces (e.g., tap110 in bridge mode with static IP).
# - Exits with an error if any configuration fails, using set -e.
# - To add new interfaces, copy and edit functions like br_tap110.

# Close on any error
set -e

# Physical interfaces
physical() {
    nic0() {
        NIC0=eth0
        ip link set dev "$NIC0" up
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
        ip link add link "$NIC0" name vlan966 type vlan id 966
        ip link set dev vlan966 up
        brctl addbr br_vlan966
        brctl stp br_vlan966 on
        brctl addif br_vlan966 vlan966
        ip link set dev br_vlan966 up
    }

    br_vlan710() {
        ip link add link "$NIC0" name vlan710 type vlan id 710
        ip link set dev vlan710 up
        brctl addbr br_vlan710
        ip link set dev br_vlan710 address
        brctl stp br_vlan710 on
        brctl addif br_vlan710 vlan710
        ip link set dev br_vlan710 up
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