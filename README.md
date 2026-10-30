# 🌐 RSyslog Web

> **Schlanker, selbstgehosteter Open-Source Syslog-Server & Live-Dashboard mit Telegram- und Ntfy-Alarmierung.**  
> Entwickelt von **Michael Schellenberger** — Freie Nutzung für jedermann (Non-Commercial, CC BY-NC 4.0).

---

## 📋 Inhaltsverzeichnis

- [Über das Projekt](#-über-das-projekt)
- [Hauptfunktionen](#-hauptfunktionen)
- [Schnellstart (Ohne Programmierkenntnisse)](#-schnellstart-ohne-programmierkenntnisse)
  - [Variante 1: Direktstart per Skript](#variante-1-direktstart-per-skript-empfohlen)
  - [Variante 2: Mit Docker & Docker Compose](#variante-2-mit-docker--docker-compose)
- [Test-Logs an den Server senden](#-test-logs-an-den-server-senden)
  - [1. Über die Web-Oberfläche](#1-über-die-web-oberfläche-am-einfachsten)
  - [2. Über das mitgelieferte Test-Skript](#2-über-das-mitgelieferte-test-skript)
  - [3. Per Terminal-Befehl (Linux / macOS / PowerShell)](#3-per-terminal-befehl-linux--macos--powershell)
- [Echte Geräte anbinden (Router, Linux, Synology NAS)](#-echte-geräte-anbinden)
- [Alarmierung einrichten (Telegram & Ntfy)](#-alarmierung-einrichten)
  - [Telegram Bot](#telegram-bot-schritt-für-schritt)
  - [Ntfy Push-Dienst](#ntfy-push-dienst-ohne-account)
- [Konfiguration (.env)](#-konfiguration-env)
- [Architektur & Code-Struktur](#-architektur--code-struktur)
- [Lizenz & Urheberrecht](#-lizenz--urheberrecht)

---

## 💡 Über das Projekt

Viele Router, Switches, Server und Smart-Home-Zentralen erzeugen Syslog-Nachrichten, aber professionelle Log-Management-Tools (wie Graylog, Splunk oder der Elastic Stack) sind oft überdimensioniert, speicherhungrig und kompliziert zu konfigurieren.

**RSyslog Web** löst dieses Problem:
- **Federleicht & superschnell:** Basiert auf modernem Python (FastAPI) und SQLite im schnellen WAL-Modus.
- **Alles in einer Lösung:** Syslog-Server (UDP & TCP) + REST/JSON-Webhook + Live-Web-Dashboard in einem einzigen Prozess.
- **Sofort einsatzbereit:** Keine Datenbank-Installationen oder komplexe Setups erforderlich.

---

## ✨ Hauptfunktionen

- 📡 **Multi-Protokoll Syslog-Empfänger:**
  - UDP- und TCP-Listener (unterstützt die Standards **RFC 3164** (BSD) und **RFC 5424** (IETF)).
  - HTTP-Webhook-Endpunkt (`POST /api/logs`) für JSON- und Text-Payloads.
- ⚡ **Echtzeit Live-Stream (WebSockets):** Eingehende Logs erscheinen sofort in der Weboberfläche — ganz ohne manuelles Neuladen der Seite.
- 🔍 **Komfortable Filter & Suche:**
  - Filter nach Log-Level (*Critical, Error, Warn, Info, Debug*).
  - Filter nach Host / Absender und App / Dienst-Name mit Autovervollständigung.
  - Volltextsuche über alle Felder.
- 📊 **Statistik-Karten:** Übersicht über Gesamtzahl, Fehlerquoten, Warnungen und aktive Hosts auf einen Blick.
- ⚙️ **Eigene Einstellungsseite (`/settings`):**
  - Alle Benachrichtigungseinstellungen für **Telegram** und **Ntfy** direkt über die Web-Oberfläche konfigurieren und sofort per Klick testen.
  - **Präzise Alarm-Bedingungen:** Konfigurierbar, ab welchem Level alarmiert wird (*Warnung, Fehler, Nur Kritisch*) und ab welcher Häufigkeit (*sofort beim 1. Fehler, ab 2 Fehlern innerhalb von 5 Min, ab 3 Fehlern...*).
  - **Host-Blacklist:** Bestimmte Entwicklungs- oder Test-Geräte gezielt von Alarmen ausschließen.
  - **Drosselung & Spamschutz:** Einstellbare Mindestpause zwischen Wiederholungen.
  - Einstellungen werden persistent in der SQLite-Datenbank gespeichert – kein manuelles Editieren von Textdateien nötig!
- 🚨 **Automatische Alarmierung (Telegram & Ntfy):**
  - Benachrichtigt auf dein Smartphone via **Telegram-Bot** und/oder **Ntfy Push-Dienst**.
  - Intelligente Vorfall-Aggregation mit Angabe der Häufigkeit im Alarmtext.
- 💾 **Export-Funktion:** Ein-Klick-Download aller gefilterten Logs als **CSV** (Excel-kompatibel) oder **JSON**.
- 🧹 **Bereinigen:** Datenbank kann bei Bedarf direkt über das Dashboard geleert werden.
- 🏷️ **Dynamische Revisionsanzeige:** Versionsnummer und Git-Commit werden im Dashboard automatisch aktualisiert.

---

## 🚀 Schnellstart (Ohne Programmierkenntnisse)

Du musst weder Python noch Web-Entwicklung beherrschen, um RSyslog Web zu nutzen.

### Variante 1: Direktstart per Skript (Empfohlen)

Öffne dein Terminal (macOS Terminal oder Linux Konsole), wechsle in den Projektordner und führe folgenden Befehl aus:

```bash
./start.sh
```

Das Skript erledigt automatisch alles für dich:
1. Es richtet die Python-Umgebung ein.
2. Es installiert die notwendigen Bibliotheken.
3. Es erzeugt eine Standardkonfiguration.
4. Es startet den Server.

Öffne anschließend einfach deinen Browser unter:
👉 **[http://localhost:8000](http://localhost:8000)**

---

### Variante 2: Mit Docker & Docker Compose

Falls du Docker installiert hast (z. B. auf einem Server, Raspberry Pi oder NAS):

```bash
docker compose up -d
```

Der Container startet im Hintergrund und leitet Port 8000 (Web) sowie Port 514 und 5514 (Syslog UDP/TCP) weiter.

---

## 🧪 Test-Logs an den Server senden

Damit du direkt ausprobieren kannst, wie Logs im Dashboard aussehen und wie Filter funktionieren:

### 1. Über die Web-Oberfläche (Am einfachsten)
Klicke im Dashboard oben rechts auf den blauen Button **"Test-Log"**. Dort kannst du Level, Absender und Nachricht frei eingeben und absenden.

---

### 2. Über das mitgelieferte Test-Skript
Führe im Terminal einfach folgendes aus:

```bash
# Sendet ein buntes Bündel realistischer Test-Logs (Info, Warnung, Fehler, Kritisch)
python test_sender.py
```

Du kannst auch gezielt ein einzelnes Log senden:
```bash
python test_sender.py --level CRITICAL --message "Festplatte /dev/sda1 hat Lesefehler"
```

---

### 3. Per Terminal-Befehl (Linux / macOS / PowerShell)

#### Mit `curl` (HTTP Webhook):
```bash
curl -X POST http://localhost:8000/api/logs \
  -H "Content-Type: application/json" \
  -d '{"host": "webserver-01", "app": "nginx", "level": "ERROR", "message": "502 Bad Gateway auf /checkout"}'
```

#### Mit `nc` (Netcat über UDP):
```bash
# macOS / Linux (Port 5514)
echo "<34>Oct 30 18:30:00 srv01 backup-service: Backup erfolgreich abgeschlossen" | nc -u -w1 127.0.0.1 5514
```

#### Mit dem Linux Standard-Befehl `logger`:
```bash
logger -n 127.0.0.1 -P 5514 -p local0.err -t mein-dienst "Kritischer Datenbankfehler aufgetreten!"
```

---

## 📡 Echte Geräte anbinden

Um Logs von deinen echten Geräten im Netzwerk an RSyslog Web weiterzuleiten:

| Gerät | Einstellung | Empfohlene Konfiguration |
| :--- | :--- | :--- |
| **Fritz!Box / AVM** | System -> Ereignisse -> Push-Service | Server-IP deines Rechners, Port `5514` (oder `514`) UDP |
| **Linux Server** | `/etc/rsyslog.conf` | `*.* @IP-DEINES-SERVERS:5514` (am Ende der Datei einfügen, danach `systemctl restart rsyslog`) |
| **Synology / QNAP** | Protokoll-Center -> Protokollsendung | Server-IP, UDP, Port `5514`, Format: IETF (RFC 5424) |
| **Docker Daemon** | `/etc/docker/daemon.json` | `"log-driver": "syslog", "log-opts": {"syslog-address": "udp://127.0.0.1:5514"}` |

> ℹ️ **Hinweis zu Port 514 vs. 5514:**  
> Unter Linux und macOS dürfen Ports unter 1024 standardmäßig nur mit Administratorrechten (`sudo`) geöffnet werden. Deshalb nutzt RSyslog Web standardmäßig Port **5514**. Wenn du den klassischen Standardport **514** nutzen möchtest, kannst du die Anwendung entweder mit `sudo` starten, Docker nutzen oder den Port in deiner `.env`-Datei anpassen.

---

## 🔔 Alarmierung einrichten

RSyslog Web kann dich automatisch benachrichtigen, sobald ein Log mit Level **ERROR** oder **CRITICAL** empfangen wird.

### Telegram Bot (Schritt für Schritt)
1. Öffne die Telegram-App und suche nach dem Bot **`@BotFather`**.
2. Sende `/newbot` und folge den Anweisungen, um deinen Bot zu benennen.
3. Du erhältst einen **Bot-Token** (z. B. `7123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ`).
4. Um deine eigene Chat-ID zu erfahren, schreibe dem Bot **`@userinfobot`** auf Telegram. Er antwortet sofort mit deiner numerischen Id (z. B. `123456789`).
5. Trage beide Werte in deine `.env`-Datei ein:
   ```env
   TELEGRAM_BOT_TOKEN=7123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ
   TELEGRAM_CHAT_ID=123456789
   ```
6. Starte RSyslog Web neu — fertig! Über den Button **"Alarm testen"** im Dashboard kannst du die Verbindung sofort prüfen.

### Ntfy Push-Dienst (Ohne Account)
[Ntfy](https://ntfy.sh) ist ein extrem einfacher, kostenloser Open-Source Push-Dienst:
1. Installiere die kostenlose *ntfy* App auf deinem Smartphone (iOS oder Android).
2. Abonniere ein beliebiges, geheimes Thema, z. B. `mein-syslog-alarm-8472`.
3. Trage dieses Thema in die `.env`-Datei ein:
   ```env
   NTFY_TOPIC=mein-syslog-alarm-8472
   ```

---

## ⚙️ Konfiguration (.env)

Alle Einstellungen können in der Datei `.env` angepasst werden:

```ini
# Web Dashboard
WEB_HOST=0.0.0.0
WEB_PORT=8000

# Syslog Empfänger Ports
SYSLOG_UDP_PORT=5514
SYSLOG_TCP_PORT=5514
SYSLOG_BIND_HOST=0.0.0.0

# Datenbank
DATABASE_PATH=syslog.db

# Alarmierung
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
NTFY_TOPIC=
NTFY_SERVER=https://ntfy.sh

# Alarmschwelle (CRITICAL, ERROR, WARN, INFO)
ALERT_MIN_LEVEL=ERROR

# Spamschutz: Mindestpause in Sekunden zwischen Alarmen gleicher Quelle
ALERT_THROTTLE_SECONDS=10

# GitHub Link im Dashboard
GITHUB_URL=https://github.com/Schello805/rsyslog
```

---

## 🏗️ Architektur & Code-Struktur

```
RSyslog/
├── main.py              # FastAPI Web-Server, REST Endpunkte & WebSockets
├── syslog_server.py     # Asynchroner UDP & TCP Syslog-Listener
├── syslog_parser.py     # RFC 3164 / RFC 5424 Syslog Parser
├── database.py          # SQLite Speicherschicht (WAL-Modus, Indizes, Filter)
├── alerter.py           # Telegram- & Ntfy-Benachrichtigungsdienst mit Spamschutz
├── config.py            # Zentrale Konfigurationsverwaltung (.env)
├── version.py           # Automatische Git- & Versionsnummer-Erkennung
├── templates/
│   └── index.html       # Modernes Dashboard (Tailwind CSS, Dark Mode, Lucide Icons)
├── test_sender.py       # Test-Tool zum Senden von Demo-Logs
├── start.sh             # 1-Klick Start-Skript für Linux & macOS
├── Dockerfile           # Container-Definition
├── docker-compose.yml   # 1-Klick Docker Bereitstellung
├── requirements.txt     # Python Abhängigkeiten
├── LICENSE              # Open-Source Lizenz (CC BY-NC 4.0)
└── README.md            # Diese Dokumentation
```

---

## ⚖️ Lizenz & Urheberrecht

Dieses Projekt ist **Open Source** unter der **Creative Commons Attribution-NonCommercial 4.0 International (CC BY-NC 4.0)** Lizenz.

**Urheber:** Michael Schellenberger  
**Bedingungen:**
- ✅ **Freie Nutzung:** Privatpersonen, gemeinnützige Organisationen, Forschung und Bildung dürfen das Projekt uneingeschränkt und kostenlos nutzen, verändern und weitergeben.
- ❌ **Nicht-kommerziell:** Eine direkte kommerzielle Vermarktung oder der Weiterverkauf als kostenpflichtiges Produkt ist ohne ausdrückliche schriftliche Zustimmung des Urhebers nicht gestattet.
- 🏷️ **Namensnennung:** Der Urhebervermerk (Michael Schellenberger) muss erhalten bleiben.


## Ressourcen überwachen: Linux, Proxmox VE und PBS

Der rsyslog-Weiterleitungsbefehl überträgt Ereignisse. Das zusätzliche Skript
`scripts/resource-monitor.py` misst CPU, RAM und Speicherplatz auf dem Linux-Gerät.
Es benötigt Python 3, `logger`, rsyslog und systemd. Auf jedem zu überwachenden
Host separat installieren; Gäste werden durch eine Installation auf dem Host
nicht einzeln überwacht. QNAP und UniFi benötigen eine andere Anbindung.

Auf Debian/Ubuntu sowie Proxmox VE/PBS als root (sonst `sudo` voranstellen):

```bash
apt update && apt install -y rsyslog python3
printf '%s\n' '*.* @192.168.1.163:5514' > /etc/rsyslog.d/99-remote.conf
rsyslogd -N1 && systemctl enable --now rsyslog && systemctl restart rsyslog
logger -p user.warning -t verbindungstest 'Test vom Linux-/Proxmox-Host'
```

Die IP durch die Adresse deines RSyslog-Servers ersetzen. `@` verwendet UDP,
`@@` TCP. Proxmox-Root-Shells benötigen kein `sudo`. Weitergeleitet werden die
von rsyslog verarbeiteten Meldungen, nicht automatisch sämtliche Proxmox-Dateilogs.

Im kopierten Projektordner startet der interaktive Installer:

```bash
sudo bash scripts/install-monitor.sh
```

Er fragt Serveradresse, Port, Schwellen, Mindestdauer, Wiederholungsintervall und
Dateisystempfade ab, prüft Messwerte und installiert Dienst und Timer.
Einstellungen liegen in `/etc/rsyslog-resource-monitor.json`.
Bestehende Weiterleitungen zum selben Ziel entfernen, damit keine Duplikate entstehen.

Standard: Prüfung etwa jede Minute; CPU und RAM ab 90 % für mindestens 10 Minuten,
Speicherplatz ab 90 % sofort bei der nächsten Prüfung. CPU wird über eine Sekunde
gemessen; RAM anhand `MemAvailable`, damit freigebbarer Cache berücksichtigt wird.
Warnungen wiederholen sich alle 30 Minuten. Bei Unterschreitung der Schwelle
erscheint einmal eine Entwarnung. Während ausgeschalteter Hosts wird nicht gemessen.

Standardmäßig wird nur `/` geprüft. Weitere Pfade und Schwellen über
`systemctl edit rsyslog-resource-monitor.service` konfigurieren, beispielsweise:

```ini
[Service]
ExecStart=
ExecStart=/usr/bin/python3 /usr/local/sbin/rsyslog-resource-monitor.py --cpu 90 --ram 90 --disk 90 --duration 600 --repeat 1800 --mount / --mount /mnt/backup
```

Danach `systemctl daemon-reload` ausführen. Nur vorhandene, tatsächlich eingehängte
Volumes angeben. ZFS-Poolbelegung, Thin-Pools und PBS-Datastore-Gesundheit sind keine
separaten Prüfungen; das Skript misst die Dateisystembelegung der angegebenen Pfade.
Container werden automatisch mit systemd erkannt. Unter cgroup v2 berücksichtigt das Skript CPU-Quota, CPU-Zuordnung und RAM-Limits (einschließlich sichtbarer Eltern-Limits). RAM nutzt den Working Set ohne inaktiven Dateicache. cgroup v1 wird mit Fehler abgelehnt; es werden keine scheinbaren Containerwerte ausgegeben. Bei Bedarf Messbereich `cgroup` explizit im Installer wählen.

Im RSyslog-Dashboard nach Dienst **resource-monitor** filtern. Für Push-Alarme die
Alarmschwelle auf **WARN** und die Häufigkeit auf **sofort** stellen: Die Mindestdauer
prüft bereits das Skript. Entwarnungen kommen als NOTICE im Dashboard an; bei einer
WARN-Alarmschwelle lösen sie keinen Push aus.

Status: `systemctl status rsyslog-resource-monitor.timer` und
`journalctl -u rsyslog-resource-monitor.service`. Deaktivieren:
`systemctl disable --now rsyslog-resource-monitor.timer`.


## Debian-Webapp per Einzeiler installieren

Für Debian 12+ mit systemd, als root oder mit sudo. Dieser Befehl funktioniert,
sobald die neuen Dateien im GitHub-Branch `main` veröffentlicht sind:

```bash
curl -fsSL https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/install-debian.sh | sudo bash
```

Falls curl fehlt: `sudo apt update && sudo apt install -y curl`.
Im bereits kopierten Projekt: `sudo bash scripts/install-debian.sh`.
Der Installer lädt den Branch `main` aus GitHub, installiert Python-Abhängigkeiten
in `/opt/rsyslog-web/.venv` und richtet den Dienst `rsyslog-web` mit eigenem
Systembenutzer ein. Eine vorhandene Installation wird nicht überschrieben.
Konfiguration: `/etc/rsyslog-web.env`; Datenbank: `/var/lib/rsyslog-web/syslog.db`.
Dashboard: `http://SERVER-IP:8000`; Syslog: UDP/TCP 5514.
Firewall passend zum eigenen LAN einrichten; die App hat weiterhin keine Anmeldung.
Status: `systemctl status rsyslog-web`; Logs: `journalctl -u rsyslog-web`.

Monitoring auf jedem Linux-/Proxmox-Gerät (nach Veröffentlichung):

```bash
curl -fsSL https://raw.githubusercontent.com/Schello805/rsyslog/main/scripts/install-monitor.sh | sudo bash
```

Nur Hosts mit empfangenem `resource-monitor: HEARTBEAT` werden auf Ausfälle überwacht.
Nach 5 Minuten ohne Heartbeat erzeugt der Server einmal ERROR, beim nächsten
Heartbeat einmal WARN als Entwarnung. Diese Meldungen folgen den allgemeinen
Alarmregeln; für sofortige Pushs Schwelle WARN und Häufigkeit 1 wählen.
Der Status erscheint im Dashboard und unter `/api/hosts`. Normale stille Syslog-
Absender (z. B. QNAP/UniFi ohne Agent) werden nicht als ausgefallen eingestuft.
Heartbeats bleiben über Neustarts der Webapp gespeichert.

Die Aufbewahrungsfrist aus den Einstellungen wird beim Start und stündlich geprüft.
0 deaktiviert die Bereinigung. Freie SQLite-Seiten werden wiederverwendet;
kein blockierendes VACUUM während des Betriebs. UDP hat eine Warteschlange von 2000
Meldungen und vier Verarbeitungs-Worker. Bei Überlast werden weitere UDP-Meldungen
gezählt und verworfen. TCP verarbeitet mit Rückstau, erlaubt höchstens 64 gleichzeitige Verbindungen und begrenzt Meldungen auf
64 KiB (Leerlauf-Timeout: 120 Sekunden). Jeder Browser hat maximal 100 ausstehende Meldungen; langsame Verbindungen
werden geschlossen und verbinden sich über die bestehende UI automatisch erneut.


## Oberfläche nach Aufgaben

Der Einstieg zeigt einen kompakten Überblick mit Erreichbarkeit und gespeicherten
Fehlermeldungen. Unter „Logs prüfen“ stehen Suche und Meldungen im Mittelpunkt;
weitere Filter, Export und Löschaktionen werden bei Bedarf geöffnet. „Gerätestatus“
führt vom Host direkt zu seinen Logs. Die Einrichtungsseite zeigt nach Auswahl des
Systems nur die passende Anleitung. Benachrichtigungskanäle, Alarmregeln und
Aufbewahrung sind getrennte Bereiche. Bereiche lassen sich per URL-Fragment
(`/#logs`, `/#devices`, `/settings#rules`) direkt öffnen.


## Revision im Footer

Der Footer zeigt automatisch `v1.0.0 · Rev. N (Commit)`. N ist die Anzahl der
Commits des installierten Branches und steigt bei jedem normalen Commit.
Die Anzeige aktualisiert sich beim Laden und jede Minute, auch ohne Serverneustart.
Der Debian-Installer lädt die vollständige Git-Historie und speichert für den
Dienstbenutzer einen Revisions-Fallback. Shallow Clones haben nur eine verkürzte
Commit-Anzahl; bei eigenen Installationen daher vollständig klonen.
Ein GitHub-Push aktualisiert keine bereits auf anderen Geräten installierte App.
