# RSyslog Web

Ein schlanker Syslog-Server mit Weboberfläche für dein Heimnetz oder Homelab.
Empfängt Logs über UDP, TCP und HTTP, zeigt sie live an und meldet Fehler über
Telegram oder Ntfy. CPU, RAM und Speicherplatz lassen sich auf Linux-Geräten
mit dem optionalen Monitoring-Skript überwachen.

## Installation auf Debian

Für **Debian 12 oder neuer mit systemd**, auch in einer Debian-VM oder einem
entsprechenden Container. Auf dem Rechner ausführen, auf dem die Webapp laufen soll:

```bash
curl -fsSL https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/install-debian.sh | sudo bash
```

Falls `curl` fehlt: `sudo apt update && sudo apt install -y curl`.
In einer Root-Shell kannst du `sudo` weglassen.

Öffne anschließend **`http://SERVER-IP:8000`**.

Der [Installer](scripts/install-debian.sh) richtet einen eigenen Systembenutzer und
den Dienst `rsyslog-web` ein. Er überschreibt keine vorhandene Installation.

## Update

Auf dem Rechner mit der Debian-Installation ausführen:

```bash
curl -fsSL https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/update-debian.sh | sudo bash
```

Das [Update-Skript](scripts/update-debian.sh) lädt den aktuellen Stand, sichert App,
Datenbank und Konfiguration, startet den Dienst neu und prüft die neue Revision.
Bei einem Fehler nach dem Wechsel stellt es App und Datenbank automatisch wieder her.
Sicherungen liegen unter `/var/backups/rsyslog-web-*` und werden nicht automatisch gelöscht.
Die Dienst-Konfiguration bleibt erhalten. Eigene Datenbankpfade werden vor dem Update
abgelehnt; dieses Skript unterstützt die Standardinstallation des Installers.

Nach der ersten Installation dieses Update-Skripts geht es auch lokal:

```bash
sudo bash /opt/rsyslog-web/scripts/update-debian.sh
```

## Erste Schritte

1. **Gerät anbinden:** In der Weboberfläche dein System auswählen und die Anleitung ausführen.
2. **Logs prüfen:** Eingang kontrollieren, nach Host, Dienst oder Level filtern.
3. **Benachrichtigungen:** Unter Einstellungen Telegram oder Ntfy einrichten, testen und Alarmregeln festlegen.

Empfang: **UDP/TCP Port 5514**. Die Firewall muss diesen Port für deine Geräte
freigeben. Die Weboberfläche hat derzeit keine Anmeldung; betreibe sie im vertrauenswürdigen Netz.

## CPU, RAM und Speicherplatz überwachen

Auf jedem Linux-Gerät bzw. Proxmox-VE-/PBS-Host separat ausführen:

```bash
curl -fsSL https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/install-monitor.sh | sudo bash
```

Der Installer fragt Serveradresse, Schwellen und Dateisystempfade ab.
Standard: CPU/RAM ab **90 % für 10 Minuten**, Speicherplatz ab **90 %** für `/`.
Warnungen werden nach 30 Minuten wiederholt; bei Erholung gibt es eine Entwarnung.
Für Push-Warnungen in der Webapp **WARN** und **sofort beim ersten Auftreten** wählen.

Ein Lebenszeichen wird etwa jede Minute gesendet. Nach fünf Minuten ohne Lebenszeichen
meldet RSyslog das Gerät als offline. Nur Geräte mit Monitoring-Skript werden so
überwacht; normale Syslog-Absender werden nicht automatisch als ausgefallen gewertet.

- Einstellungen: `/etc/rsyslog-resource-monitor.json`
- Status: `systemctl status rsyslog-resource-monitor.timer`
- Fehlerdiagnose: `journalctl -u rsyslog-resource-monitor.service`

VMs und Container brauchen eine eigene Einrichtung. Für Container werden unter
cgroup v2 die sichtbaren CPU-/RAM-Limits berücksichtigt; cgroup v1 wird nicht unterstützt.
Gemessen werden Dateisysteme, keine separaten ZFS-/Thin-Pool- oder Backup-Zustände.
QNAP und UniFi können ihre Ereignisse per Syslog senden; das Linux-Skript ist dafür nicht vorgesehen.

## Betrieb und Fehlerdiagnose

| Was? | Pfad oder Befehl |
|---|---|
| App | `/opt/rsyslog-web` |
| Konfiguration | `/etc/rsyslog-web.env` |
| Datenbank | `/var/lib/rsyslog-web/syslog.db` |
| Dienst prüfen | `sudo systemctl status rsyslog-web` |
| Dienst neu starten | `sudo systemctl restart rsyslog-web` |
| Servermeldungen anzeigen | `sudo journalctl -u rsyslog-web -n 50 --no-pager` |

Die Aufbewahrungsfrist stellst du in der Weboberfläche ein. Alte Logs werden beim
Start und stündlich bereinigt; **0** deaktiviert die automatische Bereinigung.
Die Revision im Footer steigt mit jedem Commit. Ein Push auf GitHub aktualisiert
bestehende Installationen erst nach Ausführen des Updates.

## Alternativen

Direkt aus dem geklonten Projekt auf Linux/macOS starten:

```bash
./start.sh
```

Oder mit Docker Compose (zuvor `.env.example` nach `.env` kopieren):

```bash
docker compose up -d --build
```

Diese Installationen werden nicht vom Debian-Update-Skript verwaltet.

## Lizenz

Von **Michael Schellenberger**. Nutzung unter [CC BY-NC 4.0](LICENSE)
mit Namensnennung und für nichtkommerzielle Zwecke.
