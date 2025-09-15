#!/bin/bash

# - Description: Orchestrates network and firewall configuration for virtual machines.
# - Executes network.sh and firewall.sh, with an optional function to restart services.
# - Ensures each script has execute permission and exits on errors using set -e.
# - To add new scripts or services, copy and edit functions like network or others.

# Close on any error
set -e

# Paths to the scripts
NETWORK_SCRIPT="/root/.services/network.sh"
FIREWALL_SCRIPT="/root/.services/firewall.sh"
MOUNT_SCRIPT="/root/.services/mount.sh"
VIRTUAL_MACHINE_SCRIPT="/root/.services/virtual-machine.sh"
CONTAINER_SCRIPT="/root/.services/container.sh"

network() {
    if [[ -f "$NETWORK_SCRIPT" ]]; then
        if [[ -x "$NETWORK_SCRIPT" ]]; then
            printf "\e[33m*\e[0m Running $NETWORK_SCRIPT...\n"
            bash "$NETWORK_SCRIPT"
            if [[ $? -ne 0 ]]; then
                printf "\e[31m*\e[0m Error: $NETWORK_SCRIPT failed to execute successfully.\n"
                exit 1
            fi
        else
            printf "\e[31m*\e[0m Error: $NETWORK_SCRIPT does not have execute permission.\n"
            exit 1
        fi
    else
        printf "\e[31m*\e[0m Error: $NETWORK_SCRIPT not found.\n"
        exit 1
    fi
}

firewall() {
    if [[ -f "$FIREWALL_SCRIPT" ]]; then
        if [[ -x "$FIREWALL_SCRIPT" ]]; then
            printf "\e[33m*\e[0m Running $FIREWALL_SCRIPT...\n"
            bash "$FIREWALL_SCRIPT"
            if [[ $? -ne 0 ]]; then
                printf "\e[31m*\e[0m Error: $FIREWALL_SCRIPT failed to execute successfully.\n"
                exit 1
            fi
        else
            printf "\e[31m*\e[0m Error: $FIREWALL_SCRIPT does not have execute permission.\n"
            exit 1
        fi
    else
        printf "\e[31m*\e[0m Error: $FIREWALL_SCRIPT not found.\n"
        exit 1
    fi
}

dhcp() {
    local SERVICE=dhcpcd
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

dns() {
    local SERVICE=dnsmasq
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

ntp() {
    local SERVICE=systemd-timesyncd
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

mount() {
    if [[ -f "$MOUNT_SCRIPT" ]]; then
        if [[ -x "$MOUNT_SCRIPT" ]]; then
            printf "\e[33m*\e[0m Running $MOUNT_SCRIPT...\n"
            bash "$MOUNT_SCRIPT"
            if [[ $? -ne 0 ]]; then
                printf "\e[31m*\e[0m Error: $MOUNT_SCRIPT failed to execute successfully.\n"
                exit 1
            fi
        else
            printf "\e[31m*\e[0m Error: $MOUNT_SCRIPT does not have execute permission.\n"
            exit 1
        fi
    else
        printf "\e[31m*\e[0m Error: $MOUNT_SCRIPT not found.\n"
        exit 1
    fi
}

virtual_machine() {
    if [[ -f "$VIRTUAL_MACHINE_SCRIPT" ]]; then
        if [[ -x "$VIRTUAL_MACHINE_SCRIPT" ]]; then
            printf "\e[33m*\e[0m Running $VIRTUAL_MACHINE_SCRIPT...\n"
            bash "$VIRTUAL_MACHINE_SCRIPT"
            if [[ $? -ne 0 ]]; then
                printf "\e[31m*\e[0m Error: $VIRTUAL_MACHINE_SCRIPT failed to execute successfully.\n"
                exit 1
            fi
        else
            printf "\e[31m*\e[0m Error: $VIRTUAL_MACHINE_SCRIPT does not have execute permission.\n"
            exit 1
        fi
    else
        printf "\e[31m*\e[0m Error: $VIRTUAL_MACHINE_SCRIPT not found.\n"
        exit 1
    fi
}

container() {
    if [[ -f "$CONTAINER_SCRIPT" ]]; then
        if [[ -x "$CONTAINER_SCRIPT" ]]; then
            printf "\e[33m*\e[0m Running $CONTAINER_SCRIPT...\n"
            bash "$CONTAINER_SCRIPT"
            if [[ $? -ne 0 ]]; then
                printf "\e[31m*\e[0m Error: $CONTAINER_SCRIPT failed to execute successfully.\n"
                exit 1
            fi
        else
            printf "\e[31m*\e[0m Error: $CONTAINER_SCRIPT does not have execute permission.\n"
            exit 1
        fi
    else
        printf "\e[31m*\e[0m Error: $CONTAINER_SCRIPT not found.\n"
        exit 1
    fi
}

others() {
    local SERVICE=
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

# Main function to orchestrate the setup
main() {
    SERVICES="
    network
    firewall
    dhcp
    dns
    ntp
    mount
    virtual_machine
    container
    "

    for SERVICE in $SERVICES
    do
        $SERVICE
        sleep 4
    done
}

# Execute main function
main

printf '\e[32m*\e[0m All scripts and services executed successfully!\n'
exit 0