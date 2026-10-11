#!/usr/bin/with-contenv bashio
# Punkt 77: seit 0.4.0 gibt es auch Läufe aus HA (Automatik)
bashio::log.info "RS DB Hygiene Manager startet (Analyse, Probelauf; Löschlauf manuell oder aus HA (Automatik), jeweils nur mit Hauptschalter)"
exec python3 /app/main.py
