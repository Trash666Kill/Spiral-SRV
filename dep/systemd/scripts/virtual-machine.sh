#!/bin/bash

# Restart libvirtd service
restart_libvirtd() {
    local SERVICE=libvirtd
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

VM123456() {
    virsh start VM123456
}

# Main function to orchestrate the setup
main() {
    restart_libvirtd
    #VM123456
}

# Execute main function
main