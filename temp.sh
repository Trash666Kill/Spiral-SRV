if ! echo "$output" | grep -qE "${BASE_VM_NAME}[[:space:]]"; then
    printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE_VM_NAME"

    # Create SpiralVM-Base
    eval "$VM_MANAGER" copy "$PRE_BASE_VM" "$BASE_VM_NAME"
    sleep 5
    eval "$VM_MANAGER" run "$BASE_VM_NAME"
    # Aguardando a Máquina Virtual iniciar
    waitobj 10.0.12.249 15 4 "$BASE_VM_NAME"

else
    printf "\e[32m*\e[0m INFO: A VM base \033[32m%s\033[0m já existe. Pulando criação.\n" "$BASE_VM_NAME"
    newvm
fi