#!/bin/bash

# Close on any error
set -e

# Paths to the scripts
NETWORK_SCRIPT="/root/.services/network.sh"
FIREWALL_FOLDER="/root/.services/firewall"
MOUNT_SCRIPT="/root/.services/mount.sh"

set_printk() {
    local PARAM="kernel.printk"
    local VALUE="4 4 1 7"

    # Attempt to set the kernel parameter value
    sysctl -w "$PARAM"="$VALUE"

    # Check the exit code of the last command
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to set parameter %s.\n" "$PARAM"
        printf "  Check if you are running the command with root privileges (sudo).\n"
        return 1 # Returns an error, but does not close the terminal
    fi

    printf "\e[32m✔\e[0m Parameter '%s' successfully set to '%s'.\n" "$PARAM" "$VALUE"
}

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
    scripts=(
        "$FIREWALL_FOLDER/a.sh"
        "$FIREWALL_FOLDER/c.sh"
    )

    for script in "${scripts[@]}"; do
        bash "$script"
        sleep 6
    done
}

dns() {
    local SERVICE=systemd-resolved
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

# Main function to orchestrate the setup
main() {
    SERVICES="
    set_printk
    dns
    network
    ntp
    firewall
    mount
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