ct602294_3390() {
    # DNAT Rules
    nft add rule inet firelux prerouting ip protocol tcp tcp dport 3390 dnat to 10.0.10.240:3389

    # Forward Rules
    nft add rule inet firelux forward ip protocol tcp tcp dport 3390 accept
}