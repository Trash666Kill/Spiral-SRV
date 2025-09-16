#!/bin/bash

# Restart lxc service
restart_lxc() {
    local SERVICE=lxc
    systemctl restart "$SERVICE"
    if [[ $? -ne 0 ]]; then
        printf "\e[31m*\e[0m Error: Failed to restart $SERVICE.\n"
        exit 1
    fi
}

CT123456() {
    lxc-start --name CT123456
}

# Main function to orchestrate the setup
main() {
    restart_lxc
    #CT123456
}

# Execute main function
main