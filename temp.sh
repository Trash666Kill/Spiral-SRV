if ! echo "$output" | grep -qE "${BASE_VM_NAME}[[:space:]]"; then
    printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE_VM_NAME"

    # Create SpiralVM-Base
    eval "$VM_MANAGER" copy "$PRE_BASE_VM" "$BASE_VM_NAME"
    # Aguardando a Máquina Virtual iniciar
    


else
    # --- CASO POSITIVO ---
    # (A VM JÁ existe)
    
    # 1. O "outro comando" para o caso positivo
    # Substitua pela ação desejada (ex: pular a criação)
    printf "\e[32m*\e[0m INFO: A VM base \033[32m%s\033[0m já existe. Pulando criação.\n" "$BASE_VM_NAME"
    # seu_comando_para_caso_positivo_aqui
fi