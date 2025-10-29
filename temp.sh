# Usamos grep -E para regex estendida.
    # A regex "${BASE_VM_NAME}[[:space:]]" procura por "SpiralVM"
    # seguido por pelo menos um espaço ou tab.
    #
    # - Isso corresponderá a: "SpiralVM     [STOPPED]"
    # - Isso NÃO corresponderá a: "SpiralVM-Pre [STOPPED]"
    
    if ! echo "$output" | grep -qE "${BASE_VM_NAME}[[:space:]]"; then
        printf "\e[33m*\e[0m ATTENTION: THE BASE VIRTUAL MACHINE \033[32m%s\033[0m DOES NOT EXIST, WAIT...\n" "$BASE_VM_NAME"
    fi