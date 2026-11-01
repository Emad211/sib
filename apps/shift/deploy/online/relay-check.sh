#!/usr/bin/env bash
# Phone-side relay check (O05), for Termux or any shell with curl. Run it on each SIM (Irancell, MCI,
# Rightel) at a few times of day during the 24-hour trial and paste the lines into the table in O05.
#   bash relay-check.sh https://shift.clinic.example 20
# It requests only /healthz: no login and no personal data.
set -u
url="${1:?usage: relay-check.sh https://public.name [rounds]}"
rounds="${2:-10}"
operator="$(getprop gsm.sim.operator.alpha 2>/dev/null || echo unknown)"
echo "time,operator,http_code,dns_s,connect_s,tls_s,first_byte_s,total_s"
for _ in $(seq 1 "$rounds"); do
    line="$(curl -s -o /dev/null --max-time 20 \
        -w '%{http_code},%{time_namelookup},%{time_connect},%{time_appconnect},%{time_starttransfer},%{time_total}' \
        "$url/healthz" || echo "000,,,,,")"
    echo "$(date +%H:%M:%S),$operator,$line"
    sleep 3
done
