#!/bin/bash

# - Description: Starts a container using lxc and optionally restarts lxc.
# - Defines a function to restart the lxc service, which exits on failure.
# - Starts the CT named CT123456 using `lxc-start`.
# - The `main` function calls the CT start routine; restart_lxc is defined but unused.
# - To manage other CTs, duplicate and edit the CT123456 function accordingly.

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