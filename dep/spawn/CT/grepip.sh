#!/bin/bash

# Disable bash history for this session
unset HISTFILE

# 1. Get hostname argument (e.g., from $NEW_CT)
HOSTNAME_ARG="$1"

# 2. Check if argument was provided
if [ -z "$HOSTNAME_ARG" ]; then
    printf "\e[31m*\e[0m ERROR: HOSTNAME NOT PROVIDED TO SCRIPT\n" >&2
    exit 1
fi

# 3. Convert to lowercase to ensure correct detection (CT-01 becomes ct-01)
HOSTNAME_LOWER="${HOSTNAME_ARG,,}"

# 4. File and Range Definitions
LEASES_FILE="/var/lib/misc/dnsmasq.leases"
RESERVATIONS_FILE="/etc/dnsmasq.d/config/reservations"
IP_RANGE_PREFIX="10.0.10"
MAX_LIMIT=248  # Upper limit of your dhcp-range

# 5. Function to check if IP is in use
is_ip_in_use() {
    local IP=$1
    # -F: Fixed string (dots are not treated as regex)
    # -w: Word match (prevents false positives like .2 matching inside .20)
    grep -Fw -q "$IP" "$LEASES_FILE" || grep -Fw -q "$IP" "$RESERVATIONS_FILE"
}

# 6. Logic for Even (VM) / Odd (CT) allocation
if [[ "$HOSTNAME_LOWER" == ct* ]]; then
    # CT -> ODD numbers (start at 3, step 2)
    START=3
    STEP=2
elif [[ "$HOSTNAME_LOWER" == vm* ]]; then
    # VM -> EVEN numbers (start at 2, step 2)
    START=2
    STEP=2
else
    # Fallback: Standard sequential starting from 2
    START=2
    STEP=1
fi

# 7. Search loop (respecting MAX_LIMIT)
for ((i=START; i<=MAX_LIMIT; i+=STEP)); do
    CANDIDATE_IP="$IP_RANGE_PREFIX.$i"
    
    if ! is_ip_in_use "$CANDIDATE_IP"; then
        # SUCCESS: Print only the clean IP for capture
        echo "$CANDIDATE_IP"
        exit 0
    fi
done

# 8. ERROR: No available IP found (Message goes to stderr >&2)
printf "\e[31m*\e[0m ERROR: NO AVAILABLE ADDRESSES FOUND FOR %s IN RANGE %s.2-%s\n" "$HOSTNAME_ARG" "$IP_RANGE_PREFIX" "$MAX_LIMIT" >&2
exit 1