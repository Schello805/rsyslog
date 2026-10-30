# RSyslog Web

Ein schlanker Syslog-Server mit Weboberfläche für dein Heimnetz oder Homelab.
Empfängt Logs über UDP, TCP und HTTP, zeigt sie live an und meldet Fehler über
Telegram oder Ntfy. CPU, RAM und Speicherplatz lassen sich auf Linux-Geräten
mit dem optionalen Monitoring-Skript überwachen.

## Installation auf Debian

Für **Debian 12 oder neuer mit systemd**, auch in einer Debian-VM oder einem
entsprechenden Container. Auf dem Rechner ausführen, auf dem die Webapp laufen soll:

Als root auf dem Debian-Rechner:

```bash
apt update && apt install -y git
git clone https://github.com/Schello805/rsyslog.git /opt/rsyslog-web
cd /opt/rsyslog-web
bash scripts/install-debian.sh
```

Wenn das Repository bereits unter `/opt/rsyslog-web` liegt:

```bash
cd /opt/rsyslog-web
git pull --ff-only
bash scripts/install-debian.sh
```

Der Installer kann damit auch eine unvollständige Installation einrichten.
Eine vorhandene `.env` und lokale `syslog.db` werden bei der ersten Einrichtung
des Dienstes übernommen, sofern im Datenverzeichnis noch keine Datenbank liegt.
Einen zuvor manuell gestarteten Server vor Ausführen des Installers beenden, damit die Ports frei sind.

Öffne anschließend **`http://SERVER-IP:8000`**.

Der [Installer](scripts/install-debian.sh) richtet einen eigenen Systembenutzer und
die Dienste `rsyslog-web` und `rsyslog` ein. Der Linux-Syslog-Dienst wird automatisch mitinstalliert und gestartet. Vorhandener Quellcode wird beim erneuten Ausführen nicht überschrieben.

## Update

Im installierten Git-Projekt als root:

```bash
cd /opt/rsyslog-web
bash scripts/update-debian.sh
```

Der Updater installiert außerdem einen fehlenden `rsyslog`-Dienst nach.
Er holt nur neue Git-Objekte von `main`. Ist der Stand bereits aktuell,
beendet er sich ohne Neustart. Bei unveränderten und konsistenten Abhängigkeiten
wird die Python-Umgebung ohne erneute Paketinstallation übernommen. Wurde die App bisher nur mit `start.sh` betrieben,
zuerst den Installer wie oben ausführen, um den systemd-Dienst einzurichten.

Das [Update-Skript](scripts/update-debian.sh) lädt den aktuellen Stand, sichert App,
Datenbank und Konfiguration, startet den Dienst neu und prüft die neue Revision.
Bei einem Fehler nach dem Wechsel stellt es App und Datenbank automatisch wieder her.
Sicherungen liegen unter `/var/backups/rsyslog-web-*` und werden nicht automatisch gelöscht.
Die Dienst-Konfiguration bleibt erhalten. Eigene Datenbankpfade werden vor dem Update
abgelehnt; dieses Skript unterstützt die Standardinstallation des Installers.

## Updates direkt im Footer

Der Footer prüft GitHub alle fünf Minuten und zeigt neue Versionen an.
Mit **Update starten** öffnest du den Update-Dialog. Den nötigen Schlüssel auf dem
Server als root anzeigen:

```bash
cat /etc/rsyslog-web-update.key
```

Nach Eingabe startet ein separater systemd-Dienst das vorhandene Update-Skript
mit Sicherung und Wiederherstellung. Die Webapp startet kurz neu; der Footer
zeigt den Status und lädt die Seite nach erfolgreichem Update neu.
Der Schlüssel wird nicht im Browser gespeichert und nicht von der API ausgegeben.

**Bestehende Installationen:** Einmal als root aktualisieren und den Update-Dienst einrichten:

```bash
bash /opt/rsyslog-web/scripts/update-debian.sh
bash /opt/rsyslog-web/scripts/setup-web-updates.sh
systemctl restart rsyslog-web
```

Der zusätzliche Einrichtungsschritt ist für alte Updater nötig; neue Installationen
und künftige Updates richten den Dienst automatisch ein.
Ohne diese Einrichtung (z. B. bei Docker oder `start.sh`) wird die verfügbare
Version angezeigt; die Installation erfolgt dort weiter über den jeweiligen Weg.
Das Webfrontend erhält keine allgemeinen Root- oder sudo-Rechte.

Fehlerdiagnose: `journalctl -u rsyslog-web-update` und als root
`cat /var/lib/rsyslog-web-update/update.log`. Ein Fehler wird im Footer angezeigt;
bei einem fehlgeschlagenen App-Wechsel versucht das Update-Skript die Wiederherstellung.

## Erste Schritte

1. **Gerät anbinden:** In der Weboberfläche dein System auswählen und die Anleitung ausführen.
2. **Logs prüfen:** Alle Filter sind dauerhaft sichtbar. Spaltenköpfe sortieren Zeit, Schweregrad, Host, Dienst und Nachricht auf- oder absteigend – auch über mehrere Seiten und im Export.
3. **Benachrichtigungen:** Unter Einstellungen Telegram oder Ntfy einrichten, testen und Alarmregeln festlegen.

Empfang: **UDP/TCP Port 5514**. Die Firewall muss diesen Port für deine Geräte
freigeben. Die Weboberfläche hat derzeit keine Anmeldung; betreibe sie im vertrauenswürdigen Netz.

## UniFi-Ereignisse auswerten

In UniFi Network unter **Integration → System Logging / SIEM** den RSyslog-Server
als Ziel mit Port **5514** eintragen. UniFi-CEF wird für neue Meldungen automatisch
ausgewertet: betroffenes Gerät, Ereignisname und ID, Kategorie und Schweregrad.
In „Logs prüfen“ nach Host oder Dienst (z. B. `UniFi / System / Devices`) filtern;
Ereignisnamen und IDs lassen sich über die Suche finden. Weitere Felder und der
meldende Controller stehen in den Log-Details. Originalmeldungen bleiben erhalten.

| CEF-Schweregrad | RSyslog-Level |
|---|---|
| 0–3 / Low / Unknown | INFO |
| 4–6 / Medium | WARN |
| 7–8 / High | ERROR |
| 9–10 / Very-High | CRITICAL |

CEF-Level haben Vorrang vor dem äußeren Syslog-Level. Ungültige Level behalten
den bisherigen Level bei. Ist kein betroffenes Gerät angegeben, wird der meldende
Controller bzw. der Syslog-Absender verwendet. Vorhandene Logs werden nicht
rückwirkend geändert. Quellen: [UniFi-Export](https://help.ui.com/hc/en-us/articles/33349041044119-UniFi-System-Logs-SIEM-Integration),
[CEF-Standard](https://docs.microfocus.com/doc/2097/25.4/siemcefimplementationstandard).

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

## Auf einem PVE-Host für alle Gäste einrichten

Auf **jedem PVE-Host separat** als root ausführen. Das Skript installiert rsyslog
und die Weiterleitung auf laufenden Debian-/Ubuntu-VMs und Debian-basierten
LXC-Containern des Hosts. VMs benötigen einen aktivierten, laufenden QEMU Guest Agent.
Gestoppte Gäste und Vorlagen bleiben unverändert. Windows und andere Distributionen
werden nicht unterstützt. Fehlende Voraussetzungen und Fehler stehen im Abschlussbericht.

```bash
apt update && apt install -y git
git clone https://github.com/Schello805/rsyslog.git /opt/rsyslog-tools
cd /opt/rsyslog-tools
python3 scripts/install-pve-guests.py --server 192.168.1.158 --dry-run
python3 scripts/install-pve-guests.py --server 192.168.1.158
```

Die Server-IP anpassen. Ist das Tools-Repository bereits vorhanden, dort zunächst
`git pull --ff-only` ausführen. `--dry-run` prüft die Voraussetzungen ohne Installation.
Zusätzliche Möglichkeiten:

```bash
# Auch CPU, RAM und Speicherplatz mit Standardschwellen überwachen
python3 scripts/install-pve-guests.py --server 192.168.1.158 --monitor
# Nur bestimmte Gäste oder einzelne Gäste ausschließen
python3 scripts/install-pve-guests.py --server 192.168.1.158 --only 101,102
python3 scripts/install-pve-guests.py --server 192.168.1.158 --exclude 103
```

`--monitor` setzt die Monitoring-Einstellungen auf die oben genannten Standards.
Die Installation erfolgt nacheinander und benötigt Paketquellen-Zugriff in den Gästen.
Der PVE-Host selbst wird dabei nicht verändert; dafür separat den Monitoring-Installer nutzen.
Ein fehlerhafter oder übersprungener Gast verhindert nicht die Bearbeitung der übrigen.
Bei unvollständiger Installation endet das Skript mit Exitcode 1.
Vorhandene Weiterleitungen zum gleichen Ziel können doppelte Meldungen erzeugen.

## Betrieb und Fehlerdiagnose

| Was? | Pfad oder Befehl |
|---|---|
| App | `/opt/rsyslog-web` |
| Konfiguration | `/etc/rsyslog-web.env` |
| Datenbank | `/var/lib/rsyslog-web/syslog.db` |
| Dienst prüfen | `sudo systemctl status rsyslog-web` |
| Dienst neu starten | `sudo systemctl restart rsyslog-web` |
| Servermeldungen anzeigen | `sudo journalctl -u rsyslog-web -n 50 --no-pager` |

HTTP-Zugriffslogs der Webapp sind deaktiviert, damit die Weiterleitung der eigenen
Systemlogs keinen Kreislauf im Live-Dashboard erzeugt. Eine IP vor `GET /api/...`
in älteren Meldungen bezeichnet den HTTP-Client, nicht das Syslog-Ziel.

Die Aufbewahrungsfrist stellst du in der Weboberfläche ein. Alte Logs werden beim
Start und stündlich bereinigt; **0** deaktiviert die automatische Bereinigung.
Version und Revision steigen mit jedem Commit, z. B. `v1.0.15 · Rev. 15`. Ein Push auf GitHub aktualisiert
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


Die Startseite zeigt Meldungen, Warnungen und Fehler der letzten 24 Stunden,
die letzten Ereignisse und alle bekannten Absender. „Aktiv“ bedeutet hier:
mindestens eine Meldung in 15 Minuten. Nur ein empfangenes Heartbeat-Signal erlaubt
eine Aussage zur Erreichbarkeit; reine Syslog-Absender erhalten keinen erfundenen
Online-/Offline-Status. Die Zeitfenster neuer Meldungen verwenden die Empfangszeit,
damit eine falsche Uhr am sendenden Gerät die Übersicht nicht verfälscht.


Unter **Einstellungen → System** findest du Speicherbelegung des Datenbank-Dateisystems,
Datenbankgröße inklusive WAL, gespeicherte Logs, Logs pro Minute (60 Sekunden und
5-Minuten-Durchschnitt), bekannte und aktive Absender, App-Laufzeit sowie Empfangs-
und Überwachungsstatus. Die Werte aktualisieren sich alle 10 Sekunden, solange der
Bereich geöffnet ist. Im Überblick steht die Empfangsrate direkt bei den Meldungen.
Speicherwerte beziehen sich auf den RSyslog-Server; die Lograte enthält Heartbeats,
die unter System separat ausgewiesen werden.


Auf der Logseite kannst du unter **Hosts ausschließen** mehrere Absender ausblenden.
Die Auswahl wird pro Browser gespeichert und gilt auch für CSV-/JSON-Exporte,
Sortierung und Seitenwechsel. Einzelne Hosts lassen sich über die Markierungen
wieder einblenden; „Filter zurücksetzen“ zeigt alle wieder an. Speicherung,
Alarmierung und Systemkennzahlen bleiben unverändert.


Eigene Hostnamen vergibst du unter **Gerätestatus → Benennen** oder direkt unter
dem Host-Filter bei einem ausgewählten Absender. Anzeigenamen gelten zentral für
alle Browser und bereits gespeicherte Meldungen, sind durchsuchbar und erscheinen
im Export. Der ursprüngliche Host bleibt die eindeutige Kennung für Filter und
Ausschlüsse. Ein leeres Namensfeld setzt die Anzeige zurück.


## Zusatzregeln für Geräte-Meldungen

Unter **Einstellungen → Zusatzregeln** kannst du etwa „Update verfügbar“ als Vorlage
hinzufügen. Die Regel reagiert auf eingehende Geräte-Logs, auch wenn sie nur INFO
haben. Suchtexte lassen sich an die tatsächlichen Meldungen deiner Geräte anpassen;
Ausschlusstexte verhindern Treffer wie „keine Updates verfügbar“. Die Suche ist ohne
Beachtung der Groß-/Kleinschreibung und ohne reguläre Ausdrücke.

Optional auf einen ursprünglichen Host und einen Dienst eingrenzen. Versand erfolgt
über die aktivierten Telegram-/Ntfy-Kanäle. Jede Regel hat eine eigene Pause je Host,
Dienst und Kanal, die auch Neustarts übersteht. Fehlgeschlagene Kanäle dürfen bei
der nächsten passenden Meldung erneut versuchen zu senden. Bei mehreren passenden
Regeln wird nur die erste verwendet; für diese Meldung gilt ihre Pause anstelle der
allgemeinen Level-/Häufigkeitsregel. Globale Alarm-Hostausschlüsse gelten weiterhin.
Regeln aktivieren, bearbeiten oder entfernen und anschließend Einstellungen speichern.

Es werden keine Paketstände aktiv abgefragt: Das Gerät muss den Update-Hinweis
selbst per Syslog senden. Diese Funktion betrifft keine Updates von RSyslog Web.
