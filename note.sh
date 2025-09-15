interface() {
    # Installing required packages
    apt-get -y install bc > /dev/null 2>&1
    printf "\e[32m*\e[0m CHOOSING THE BEST AVAILABLE INTERFACE, WAIT...\n"

    # Target IP for ping
    TARGET_IP="8.8.8.8"

    # Variables to store the interface with the lowest latency, its altname, IP, netmask, and gateway
    best_interface=""
    best_latency=9999.0
    best_altname=""
    best_ip=""
    best_netmask=""
    best_gateway=""
    dest_script_path="/root/.services/network.sh"

    # Check if the destination file exists and is writable
    if [[ ! -f "$dest_script_path" ]]; then
        printf "\033[31m*\033[0m ERROR: FILE \033[32m%s\033[0m DOES NOT EXIST\n" "$dest_script_path"
        exit 1
    fi
    if [[ ! -w "$dest_script_path" ]]; then
        printf "\033[31m*\033[0m ERROR: CANNOT WRITE TO \033[32m%s\033[0m. CHECK PERMISSIONS.\n" "$dest_script_path"
        exit 1
    fi

    # Iterate over active interfaces (status UP) starting with eth, en, or enp
    for iface in $(ip -o link show | awk -F': ' '/state UP/ && ($2 ~ /^(eth|en|enp)/) {sub(/@.*/, "", $2); print $2}'); do
        # Test ping on the interface with 3 packets, capture average latency
        latency
        latency=$(ping -I "$iface" -4 -c 3 "$TARGET_IP" 2>/dev/null | awk -F'/' 'END {print $5}') || continue

        # Compare current latency with the best found so far
        if [[ -n "$latency" && $(echo "$latency < $best_latency" | bc -l) -eq 1 ]]; then
            best_latency="$latency"
            best_interface="$iface"
            # Extract the first altname of the interface
            best_altname=$(ip addr show "$iface" | awk '/altname/ {print $2; exit}')
            # Extract the IP address and netmask (CIDR) of the interface
            ip_info
            ip_info=$(ip -4 addr show "$iface" | grep 'inet' | awk '{print $2}' | head -n1)
            best_ip=$(echo "$ip_info" | cut -d'/' -f1)
            best_netmask=$(echo "$ip_info" | cut -d'/' -f2)
            # Extract the gateway for the interface
            best_gateway=$(ip route show dev "$iface" | awk '/default/ {print $3}')
        fi
    done

    # Check if a valid interface was found
    if [[ -z "$best_interface" ]]; then
        printf "\033[31m*\033[0m ERROR: NO VALID INTERFACE FOUND TO WRITE TO /etc/environment\n"
        exit 1
    fi

    # Assign to global-like variables
    NIC0="$best_interface"
    printf "\e[32m*\e[0m CHOSEN INTERFACE: \033[32m%s\033[0m, LATENCY OF \033[32m%s ms\033[0m FOR \033[32m%s\033[0m\n" "$NIC0" "$best_latency" "$TARGET_IP"

    # Write NIC0 and NIC0_ALT to /etc/environment
    if [[ -n "$best_altname" && -n "$best_interface" ]]; then
        printf "\e[32m*\e[0m WRITING ALTNAME \033[32m%s\033[0m AND INTERFACE \033[32m%s\033[0m TO /etc/environment\n" "$best_altname" "$NIC0"
        touch /etc/environment
        sed -i '/^NIC0=/d' /etc/environment
        sed -i '/^NIC0_ALT=/d' /etc/environment
        echo "NIC0=$NIC0" >> /etc/environment
        echo "NIC0_ALT=$best_altname" >> /etc/environment
    else
        printf "\033[31m*\033[0m ERROR: NO VALID INTERFACE FOUND TO WRITE TO /etc/environment\n"
        exit 1
    fi

    # Update /root/.services/network.sh with the new interface configuration
    if [[ -n "$best_ip" && -n "$best_netmask" && -n "$best_gateway" ]]; then
        # Convert CIDR to decimal netmask for ifconfig
        case "$best_netmask" in
            8) decimal_netmask="255.0.0.0" ;;
            16) decimal_netmask="255.255.0.0" ;;
            24) decimal_netmask="255.255.255.0" ;;
            32) decimal_netmask="255.255.255.255" ;;
            *) printf "\033[31m*\033[0m ERROR: UNSUPPORTED NETMASK \033[32m/%s\033[0m FOR IFCONFIG\n" "$best_netmask"; exit 1 ;;
        esac
        printf "\e[32m*\e[0m WRITING ALTNAME \033[32m%s\033[0m AND INTERFACE \033[32m%s\033[0m TO %s\n" "$best_altname" "$NIC0" "$dest_script_path"
        # Remove old configuration lines
        sed -i '/ifconfig "$NIC0" 0\.0\.0\.0/d' "$dest_script_path" || {
            printf "\033[31m*\033[0m ERROR: FAILED TO REMOVE OLD IFCONFIG LINE IN \033[32m%s\033[0m\n" "$dest_script_path"
            exit 1
        }
        sed -i '/ip route add default via 0\.0\.0\.0 dev "$NIC0"/d' "$dest_script_path" || {
            printf "\033[31m*\033[0m ERROR: FAILED TO REMOVE OLD IP ROUTE LINE IN \033[32m%s\033[0m\n" "$dest_script_path"
            exit 1
        }
        # Remove any existing NIC0_CONFIG or NIC0_DEFAULT_ROUTE lines to avoid duplicates
        sed -i '/# NIC0_CONFIG/d' "$dest_script_path"
        sed -i '/# NIC0_DEFAULT_ROUTE/d' "$dest_script_path"
        # Add new configuration lines after the last line of br_vlan710
        sed -i '/brctl addif br_vlan710 vlan710/a\        # NIC0_CONFIG\n        ifconfig "'"$NIC0"'" '"$best_ip"' netmask '"$decimal_netmask"'\n        # NIC0_DEFAULT_ROUTE\n        ip route add default via '"$best_gateway"' dev "'"$NIC0"'"' "$dest_script_path" || {
            printf "\033[31m*\033[0m ERROR: FAILED TO UPDATE \033[32m%s\033[0m WITH NEW CONFIGURATION\n" "$dest_script_path"
            exit 1
        }
    else
        printf "\033[31m*\033[0m ERROR: COULD NOT DETERMINE IP, NETMASK, OR GATEWAY FOR INTERFACE \033[32m%s\033[0m\n" "$NIC0"
        exit 1
    fi
}