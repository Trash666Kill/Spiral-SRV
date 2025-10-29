#!/bin/bash

# Run the manager and capture the output
output=$(python3 vm_manager.py list)

# Check if the output contains a line with "SpiralVM"
if echo "$output" | grep -q "SpiralVM"; then
    printf "\e[32m*\e[0m 'SpiralVM' VM found. Continuing...\n"
    
    #
    # Coloque seus comandos de continuação aqui
    #
    
else
    printf "\e[31m*\e[0m No 'SpiralVM' VM found. Aborting.\n"
    # exit 1 # Descomente esta linha se desejar que o script pare aqui
fi