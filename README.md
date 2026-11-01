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
Mit **Update starten** öffnest du den Update-Dialog und bestätigst die Installation.
Für den Betrieb im internen Netz ist kein Update-Schlüssel erforderlich. Wer auf
die App zugreifen kann, kann damit auch Updates starten. Aufrufe aus fremden
Webseiten werden durch eine Origin-Prüfung und einen festen Anfrage-Header blockiert.
Ein separater systemd-Dienst führt das feste Update-Skript mit Sicherung und
Wiederherstellung aus. Die Webapp startet kurz neu; der Footer zeigt den Status
und lädt die Seite nach erfolgreichem Update neu.

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

In der Weboberfläche unter **Geräte anbinden → Proxmox: Alle Gäste anbinden**
stehen Vorbereitung, Vorschau und Installation mit Kopierbuttons bereit. Serveradresse,
Port und die optionale Ressourcenüberwachung werden in die Befehle übernommen.

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

Neue DEBUG-Meldungen werden standardmäßig beim Empfang verworfen (UDP, TCP und
HTTP). Unter **Einstellungen → Daten aufbewahren** lässt sich das für eine
Fehlersuche deaktivieren. Anschließend speichern; die Änderung gilt sofort.
Vorhandene Logs werden nicht entfernt. Verworfene Meldungen werden weder
live angezeigt noch für Alarme verwendet.

Bei Syslog ohne Hostnamen (z. B. CCU/BusyBox) erkennt die App den Dienst-Tag
und verwendet den Namen zur Absender-IP. Manuelle Namenszuordnungen haben
Vorrang; andernfalls wird DNS mit begrenzter Wartezeit und Cache verwendet.
Ohne DNS-Eintrag bleibt die IP erhalten. Die Absender-IP wird zusätzlich in den
Metadaten gespeichert. Alte falsch zugeordnete Einträge lassen sich nur mit einer
eindeutigen Gerätezuordnung reparieren.

IP-/Namens-Doppeleinträge durch ungewöhnliche Syslog-Tags (z. B. `(systemd)`
oder abgeschnittene Prozess-IDs) werden beim Start einmalig anhand des ursprünglichen
Syslog-Headers repariert. Originalmeldungen bleiben erhalten. Meldungen ohne
eindeutigen Hostnamen behalten ihre IP-Adresse; Anzeigenamen werden nicht zum
Zusammenführen unterschiedlicher Geräte verwendet.

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

## Warnungen mit KI analysieren

Unter **Einstellungen → KI-Analyse** kannst du OpenAI- und Gemini-API-Schlüssel
hinterlegen. Modell-IDs sind vorbelegt und anpassbar. Ein Anbieter genügt. Schlüssel
werden in der lokalen Datenbank gespeichert, beim Anzeigen nicht zurückgegeben;
leere Eingaben behalten einen Schlüssel, die Löschoption entfernt ihn.

Unter **Logs prüfen → Gefilterte Warnungen analysieren** zeigt die App eine
Vorschau der höchstens 100 neuesten Warnungen und Fehler für die aktiven Filter
und Host-Ausschlüsse. Erst **Vorschau senden & analysieren** übermittelt diese
Daten an den gewählten Anbieter. Lange Texte und die Gesamtmenge werden begrenzt;
INFO/NOTICE/DEBUG werden nicht analysiert. Gleiche Meldungen werden mit Häufigkeit und Zeitspanne zusammengefasst. Die Vorschau gilt zehn Minuten.
Die Analyse zeigt die Wartezeit und wartet maximal 45 Sekunden auf den Anbieter.

Die Antwort erläutert Dringlichkeit, mögliche Ursachen, nächste Schritte und
Prüfungen. Es werden keine Befehle ausgeführt. Analysevorschläge vor Anwendung
prüfen. Die Vorschau kann interne Hostnamen, IPs und vertrauliche Inhalte enthalten;
die automatische Maskierung erkennbarer Zugangsdaten ist nicht vollständig.
API-Kosten und Datenschutzbedingungen des Anbieters beachten. App-Zugriff,
Datenbank und Backups schützen; bei Fernzugriff HTTPS verwenden.

Technik: [OpenAI Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
mit `store: false` bzw. [Gemini generateContent](https://ai.google.dev/api/generate-content).
Keine automatischen Hintergrundanalysen und keine neuen Python-Abhängigkeiten.

## Lokale Frontend-Dateien

CSS und Icons werden vollständig vom eigenen Server ausgeliefert. Zum Anzeigen
ist kein Zugriff auf Tailwind- oder Icon-CDNs erforderlich; Debian-Installation
und Update benötigen dafür kein Node.js.

Nur bei Änderungen am Frontend lokal neu bauen:

```bash
npm ci --ignore-scripts
npm run build
```

Die erzeugten Dateien unter `static/vendor` einschließlich Lizenzdateien werden
mit eingecheckt. Grundlage: [Tailwind CLI](https://v3.tailwindcss.com/docs/installation)
und [Lucide](https://lucide.dev/guide/packages/lucide).

## Geräte, Vorfälle und Speichersteuerung

**Gerätestatus** zeigt alle bekannten Geräte als Tabelle. Suche, Statusfilter und
Sortierung nach Name, Aktivität, Fehlerzahl oder Loganzahl bleiben bei laufenden
Aktualisierungen erhalten.

Unter **Vorfälle** werden gleiche Warnungen und Fehler pro Host, Dienst und Level
zusammengefasst: Anzahl sowie erstes und letztes Auftreten, mit „offen“ oder
„erledigt“. Eine neue passende Meldung öffnet einen erledigten Vorfall wieder.
Vorhandene Warnungen werden beim ersten Start übernommen. Aufbewahrung und
Speicherbereinigung entfernen auch zugehörige alte Vorfallsgruppen.

Unter **Einstellungen → System** stehen die seit App-Start verworfenen DEBUG-
und wegen Speichergrenze abgewiesenen Meldungen. UDP-Überlast wird separat gezählt.
Die Zähler beginnen nach einem Neustart wieder bei null.

**Einstellungen → Daten aufbewahren** bietet zusätzlich eine Größenbegrenzung
(0 = deaktiviert; sonst mindestens 16 MB). Aktivieren erlaubt die automatische
Bereinigung der ältesten Logs. Bei Platzmangel werden neue Meldungen abgewiesen;
die Bereinigung läuft alle 30 Sekunden in kurzen Batches. Die Grenze bezieht sich
auf belegte SQLite-Seiten. Bereits reservierter freier Datenbankplatz wird
wiederverwendet; die Datei muss dadurch nicht sofort kleiner werden. WAL-Dateien
und Backups benötigen zusätzlichen Speicher. Die Übersicht warnt ab 80 % der
Datenbankgrenze oder 90 % Dateisystembelegung; Speicherwarnungen durchlaufen auch
die eingerichteten Benachrichtigungsregeln.

Für eine unveränderte KI-Auswahl wird die gespeicherte vollständige Analyse mit
Datum und Anbieter eingeblendet. **Erneut analysieren** fordert ausdrücklich eine
neue Antwort an. Der Cache enthält höchstens 20 Analysen für sieben Tage, keine
API-Schlüssel. Er wird bei Änderungen an Auswahl, Anbieter, Modell oder Schlüssel
nicht wiederverwendet. „Alle Logs löschen“ entfernt auch Vorfälle und Analysen.

Web-Updates melden ihre Schritte im Footer: Prüfen, Herunterladen, Vorbereiten,
Sichern, Installieren, Neustart und Prüfung der neuen Revision. Die Schrittzahl
ist keine Schätzung der verbleibenden Zeit; Wiederherstellungen werden gesondert
angezeigt.

Kennzahlen werden serverseitig für zehn Sekunden gemeinsam zwischengespeichert.
Überblick und Geräteliste aktualisieren sich nur, solange ihre Ansicht sichtbar ist;
die Host-/Dienstfilter auf der Log-Seite werden alle 60 Sekunden und beim Öffnen geladen.
Speichern von Einstellungen, Umbenennen eines Hosts und Leeren der Logs löschen den Cache sofort.

Der Gerätestatus unterscheidet Syslog-Aktivität („Logs aktiv“ innerhalb von 15 Minuten)
von Online/Offline mit Lebenszeichen. Alte, als Host gespeicherte Dienst-Tags wie
`kernel:` erscheinen separat unter „Nicht zugeordnete alte Meldungen“ und zählen
nicht als Geräte. Ihre Logs bleiben über die Filter und „Logs prüfen“ zugänglich.

Die Geräteidentität eingehender Logs ist ausschließlich die empfangene Quell-IP
(UDP/TCP-Gegenstelle bzw. HTTP-Client). DNS und manuell vergebene Namen ändern
nur die Anzeige. Namen/IPs im Logtext und CEF-Gerätefelder bleiben Ereignisinhalt;
auch über einen Relay weitergeleitete Meldungen gehören zur Quell-IP des Relays.
Beim Update werden historische Logs nur anhand einer bereits gespeicherten
`source_ip` umgestellt; fehlende Quell-IPs werden nicht aus Namen rekonstruiert.

Die Geräteliste zeigt Anzeigename/DNS, Quell-IP und letzte Aktivität getrennt.
In den Logdetails stehen die gespeicherte Quell-IP und der gemeldete Hostname.
Auf der Log-Seite gibt es Zeiträume von einer Stunde bis sieben Tagen und bis zu
20 gespeicherte Ansichten pro Browser inklusive Ausschlüssen und Sortierung.

Benachrichtigungen bündeln identische Meldungen je Gerät/Dienst. Je Gerät und
Kanal werden höchstens fünf Meldungen pro Minute gesendet; ein höherer
Schweregrad durchbricht die Sperre. Erfolgreiche und fehlgeschlagene Kanäle werden
getrennt behandelt. Diese allgemeine Begrenzung gilt seit App-Start; Zusatzregeln
behalten zusätzlich ihre gespeicherte Wiederholungssperre. Alle angenommenen Logs
und Vorfälle bleiben unabhängig von unterdrückten Benachrichtigungen gespeichert.

UDP, TCP und HTTP nutzen im laufenden Dienst einen gemeinsamen Schreibpuffer
(maximal 100 Einträge, 25 ms Sammelzeit, Warteschlange 1000). Empfangsbestätigung,
Live-Anzeige und Alarmprüfung erfolgen erst nach dem Commit. Beim regulären
Beenden wird der Puffer geleert; bei abruptem Prozessabbruch können noch nicht
bestätigte Einträge im RAM verloren gehen. Bei Batchfehlern wird zurückgerollt
und jeder Eintrag einzeln versucht.

Für KI-Analysen lassen sich 25, 50 oder 100 Warnungen auswählen. „Meldungen für KI
kopieren“ öffnet die Vorschau; „Meldungen kopieren“ kopiert sie mit Analyseauftrag
für eine manuelle Nutzung, auch ohne API-Schlüssel. Erkannte Zugangsdaten werden
wie in der Vorschau maskiert. Die API-Analyse nutzt niedrigen Denkaufwand bei den
unterstützten Modellfamilien und einen Zeitrahmen von höchstens 60 Sekunden.
„Verbindung prüfen“ unter Einstellungen → KI prüft den gespeicherten Schlüssel
und Modellzugriff ohne Logdaten oder Textgenerierung. Eine erfolgreiche Prüfung
belegt keine ausreichende Generierungsquote. Anbieterfehler nennen HTTP-Status,
Modell und sichere Fehlerkategorie; Zugangsdaten werden nicht ausgegeben.

Die Modellparameter orientieren sich an den offiziellen Dokumentationen:
[OpenAI Reasoning](https://developers.openai.com/api/docs/guides/reasoning) und
[Gemini Thinking](https://ai.google.dev/gemini-api/docs/generate-content/thinking).

Im Hostfilter stehen Geräte einheitlich als `Quell-IP (Name/DNS)`, numerisch nach
IP sortiert. Ohne aufgelösten Namen steht ausdrücklich „DNS nicht aufgelöst“.
Altbestand und interne Meldungen ohne gespeicherte Quell-IP bilden eine eigene
Gruppe und werden keinem IP-Gerät zugerechnet. Die Wartung prüft alle 30 Sekunden
höchstens acht bekannte Quell-IPs reihum mit dem begrenzten DNS-Cache, sodass auch
späte DNS-Antworten ohne weitere Logmeldung in der Anzeige ankommen. DNS-Namen
führen niemals zwei Quell-IPs zusammen; manuelle Anzeigenamen haben Vorrang.

Namenslisten und Dropdowns sind alphabetisch (A–Z, deutsche Sortierung) geordnet.
Hosts werden nach Name/DNS sortiert, ersatzweise nach IP.
Die Geräteliste startet mit „Name A–Z“; andere Sortierungen bleiben auswählbar.
Zeiträume und Schweregrade behalten ihre fachliche Reihenfolge.

Als Geräte werden ausschließlich IP-Identitäten gezählt. Historische Hostnamen und
interne Absender ohne gespeicherte Quell-IP stehen separat unter „Meldungen ohne
gespeicherte Quell-IP“; sie erzeugen keine zusätzlichen Geräte- oder Offline-Zähler.
Im Hostfilter sind sie standardmäßig ausgeblendet und lassen sich über „Altbestand /
interne Absender anzeigen“ einblenden. Die Logs bleiben unverändert zugänglich.
Historische Einträge mit belegter `source_ip` werden beim Update erneut dieser IP
zugeordnet; DNS-Namensgleichheit allein führt weiterhin zu keiner Zusammenführung.

Der Überblick und die Geräteliste zeigen beim erneuten Öffnen sofort den zuletzt
empfangenen Stand aus diesem Browser und aktualisieren ihn im Hintergrund.
Ein Zeitstempel kennzeichnet gespeicherte/veraltete Daten. Der Cache enthält eine
Dashboard-Antwort (inklusive der dort sichtbaren Ereignisse), höchstens 512 KB,
maximal 24 Stunden. Browser ohne verfügbaren Speicher nutzen weiterhin den
Live-Abruf. Beim Leeren der Logs wird auch dieser lokale Cache geleert.

Unter Einstellungen → System lassen sich lokale DNS-Server (z. B. Pi-hole) und
eine optionale Suchdomain angeben. DNS-Abfragen nutzen feste Zeitlimits und den
begrenzten Hintergrund-Pool. Fehlt ein PTR-Eintrag, wird ein gemeldeter Hostname
nur dann als DNS-Name bestätigt, wenn sein A-/AAAA-Eintrag zur Quell-IP passt.

Ohne DNS-Eintrag zeigt die App einen eindeutigen, in den letzten 30 Meldungen
enthaltenen Hostnamen als „Gemeldet · nicht DNS-bestätigt“. Dafür muss die
Quell-IP im betreffenden Log gespeichert sein. Mehrere unterschiedliche Namen
hinter derselben IP werden nicht willkürlich ausgewählt; CEF-Gerätenamen werden
nicht mit dem meldenden Controller verwechselt. DNS und manuelle Namen haben
Vorrang. Die Geräteidentität bleibt immer die Quell-IP.
