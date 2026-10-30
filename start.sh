#!/usr/bin/env bash
# ==============================================================================
# RSyslog Web — Einfacher Start-Skript für Einsteiger & Fortgeschrittene
# Open-Source von Michael Schellenberger
# ==============================================================================

set -e

# Wechsle in das Verzeichnis des Skripts
cd "$(dirname "$0")"

echo "--------------------------------------------------------"
echo "  🚀 Starte RSyslog Web..."
echo "  Autor: Michael Schellenberger"
echo "--------------------------------------------------------"

# 1. Prüfe Python Installation
if command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "❌ FEHLER: Python 3 wurde nicht gefunden. Bitte installiere Python 3."
    exit 1
fi

# 2. Virtuelle Umgebung (.venv) anlegen falls noch nicht vorhanden
if [ ! -d ".venv" ]; then
    echo "📦 Erstelle Python-Umgebung (.venv)..."
    $PYTHON_CMD -m venv .venv
fi

# 3. Virtuelle Umgebung aktivieren
source .venv/bin/activate

# 4. Abhängigkeiten prüfen / installieren
echo "🔍 Überprüfe Abhängigkeiten..."
pip install -q -r requirements.txt

# 5. .env Datei erstellen falls nicht existent
if [ ! -f ".env" ]; then
    echo "⚙️  Erstelle Standard-.env aus .env.example..."
    cp .env.example .env
fi

# 6. Starte den RSyslog Server und das Dashboard
echo "--------------------------------------------------------"
echo "  ✅ Server läuft!"
echo "  🌐 Web Dashboard: http://localhost:8000"
echo "  📡 Syslog UDP:    Port 5514 (oder laut .env)"
echo "  📡 Syslog TCP:    Port 5514 (oder laut .env)"
echo "  Beenden mit:      STRG + C"
echo "--------------------------------------------------------"

python main.py
