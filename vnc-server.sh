#!/bin/bash
# Inicia Xvfb
Xvfb :1 -screen 0 1920x1080x24 +extension RANDR -extension GLX -listen tcp &
XVFB_PID=$!
sleep 3  # Aguarda o Xvfb inicializar

# Define o display e carrega recursos (ignora erros se display não estiver pronto)
export DISPLAY=:1
xrdb $HOME/.Xresources 2>/dev/null || true

# Inicia o gerenciador de janelas
openbox-session &

# Inicia o Firefox (opcional, com atraso)
sleep 2
firefox-esr &

# Mantém o script ativo (opcional, mas útil para depuração)
wait $XVFB_PID