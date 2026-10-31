(() => {
    const status = document.getElementById('footer-update-status');
    const button = document.getElementById('footer-update-button');
    if (!status || !button) return;
    const savedKeyName = 'rsyslog-update-key';
    function readKey() {
        try { return localStorage.getItem(savedKeyName) || ''; } catch { return ''; }
    }
    function forgetKey() {
        try { localStorage.removeItem(savedKeyName); } catch { /* Storage may be blocked. */ }
    }
    function saveKey(key) {
        localStorage.setItem(savedKeyName, key);
        if (localStorage.getItem(savedKeyName) !== key) throw new Error('Storage unavailable');
    }
    let updateInfo = null;
    let polling = false;
    function pendingUpdate() {
        try { return sessionStorage.getItem('rsyslog-update-pending') === '1'; } catch { return false; }
    }
    function storePending(active) {
        try {
            if (active) sessionStorage.setItem('rsyslog-update-pending', '1');
            else sessionStorage.removeItem('rsyslog-update-pending');
        } catch { /* Polling still works for this page when session storage is blocked. */ }
    }
    let waiting = pendingUpdate();
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'width:min(92vw,440px);border:1px solid #cbd5e1;border-radius:16px;padding:24px;background:#ffffff;color:#0f172a';
    dialog.innerHTML = `<form method="dialog"><h2 style="font-size:18px;font-weight:600">App aktualisieren</h2>
        <p id="update-dialog-description" style="font-size:14px;color:#475569;margin:12px 0"></p>
        <div id="update-auth">
        <p style="font-size:13px;color:#475569">Die App wird kurz neu gestartet. Logs und Einstellungen werden gesichert.</p>
        <label for="web-update-key" style="display:block;margin-top:16px;font-size:14px">Update-Schlüssel</label>
        <input id="web-update-key" type="password" autocomplete="off" required style="width:100%;box-sizing:border-box;margin:8px 0;padding:10px;background:#f8fafc;color:#0f172a;border:1px solid #cbd5e1;border-radius:8px">
        <label style="display:flex;gap:8px;align-items:center;font-size:13px"><input id="remember-update-key" type="checkbox" checked> Schlüssel in diesem Browser merken</label>
        <p style="font-size:12px;color:#64748b;margin:8px 0">Nur auf vertrauenswürdigen Geräten speichern. Wer dieses Browserprofil nutzt, kann damit Updates starten.</p>
        <p id="update-key-status" role="status" style="font-size:13px;color:#475569;margin:8px 0"></p>
        <button type="button" id="save-update-key" style="font-size:13px;color:#0369a1;margin-right:12px">Schlüssel speichern</button>
        <button type="button" id="forget-update-key" style="font-size:13px;color:#0369a1" hidden>Gespeicherten Schlüssel entfernen</button>
        </div>
        <div id="update-setup" hidden style="font-size:13px;color:#475569">
            <p>Die Update-Prüfung ist aktiv, aber der Update-Dienst fehlt noch. Für die Debian-Installation einmal als root auf dem Server ausführen:</p>
            <pre style="white-space:pre-wrap;overflow-wrap:anywhere;background:#f1f5f9;padding:12px;border-radius:8px;margin:12px 0">bash /opt/rsyslog-web/scripts/setup-web-updates.sh
systemctl restart rsyslog-web</pre>
            <p>Danach die Seite neu laden. Den Schlüssel zeigt <code>cat /etc/rsyslog-web-update.key</code>. Docker- und manuelle Installationen weiter über ihren Installationsweg aktualisieren.</p>
        </div>
        <p id="update-dialog-error" role="alert" style="font-size:13px;color:#be123c"></p>
        <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:16px">
            <button type="button" id="update-cancel">Abbrechen</button>
            <button type="submit" id="update-confirm" style="padding:8px 12px;border-radius:8px;background:#0369a1;color:white">Update starten</button>
        </div></form>`;
    document.body.append(dialog);
    const field = dialog.querySelector('#web-update-key');
    const error = dialog.querySelector('#update-dialog-error');
    const remember = dialog.querySelector('#remember-update-key');
    const forget = dialog.querySelector('#forget-update-key');
    const keyStatus = dialog.querySelector('#update-key-status');
    function persistKey() {
        const key = field.value.trim();
        if (!key) { keyStatus.textContent = 'Bitte zuerst den Update-Schlüssel eingeben.'; return false; }
        try {
            saveKey(key); forget.hidden = false;
            keyStatus.textContent = 'Schlüssel in diesem Browser gespeichert. Gültigkeit wird beim Update geprüft.';
            return true;
        } catch {
            keyStatus.textContent = 'Speichern nicht möglich: Der Browser blockiert den lokalen Speicher. Der Schlüssel gilt nur für dieses Update.';
            return false;
        }
    }
    dialog.querySelector('#save-update-key').addEventListener('click', () => {
        remember.checked = true; persistKey();
    });
    forget.addEventListener('click', () => {
        forgetKey(); field.value = ''; remember.checked = false; forget.hidden = true;
        keyStatus.textContent = 'Gespeicherter Schlüssel entfernt.';
    });
    remember.addEventListener('change', () => {
        if (!remember.checked) { forgetKey(); forget.hidden = true; keyStatus.textContent = 'Schlüssel wird nicht gespeichert.'; }
    });
    dialog.querySelector('#update-cancel').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { field.value = ''; error.textContent = ''; });

    function showJob(job) {
        if (job.state === 'queued' || job.state === 'running') {
            waiting = true;
            storePending(true);
            status.textContent = job.state === 'queued' ? 'Update wird gestartet …' : 'Update läuft …';
            button.hidden = true;
            poll();
            return true;
        }
        if (job.state === 'failed') {
            waiting = false;
            storePending(false);
            status.textContent = 'Update fehlgeschlagen · Serverstatus prüfen';
            status.title = job.message || '';
            return false;
        }
        if (job.state === 'succeeded' && waiting) {
            waiting = false;
            storePending(false);
            status.textContent = 'Update erfolgreich · Seite wird neu geladen';
            button.hidden = true;
            setTimeout(() => location.reload(), 1500);
            return true;
        }
        return false;
    }

    async function poll() {
        if (polling) return;
        polling = true;
        try {
            const response = await fetch('/api/updates/status', {cache:'no-store', signal:AbortSignal.timeout(10000)});
            if (!response.ok) throw new Error('Status nicht verfügbar');
            const job = await response.json();
            showJob(job);
        } catch (error) {
            status.textContent = 'Neustart läuft · warte auf Verbindung …';
        } finally {
            polling = false;
            if (waiting) setTimeout(poll, 3000);
        }
    }

    async function check() {
        if (waiting) { poll(); return; }
        try {
            const response = await fetch('/api/updates', {cache:'no-store', signal:AbortSignal.timeout(25000)});
            if (!response.ok) throw new Error('Update-Prüfung fehlgeschlagen');
            updateInfo = await response.json();
            if (showJob(updateInfo.job)) return;
            button.hidden = !updateInfo.available && updateInfo.can_start;
            button.textContent = updateInfo.can_start ? 'Update starten' : 'Web-Updates einrichten';
            button.title = updateInfo.can_start ? 'Neue Version installieren' : 'Einmalige Einrichtung auf dem Server anzeigen';
            if (updateInfo.job.state === 'failed') return;
            status.title = '';
            if (updateInfo.available) {
                status.textContent = `Update auf v${updateInfo.latest_version} verfügbar`;
                if (!updateInfo.can_start) status.title = 'Update verfügbar; der Update-Dienst ist noch nicht eingerichtet.';
            } else if (updateInfo.check_state === 'current') status.textContent = 'Auf aktuellem Stand';
            else if (updateInfo.check_state === 'manual') status.textContent = 'Git-Stand manuell prüfen';
            else status.textContent = 'Update-Prüfung derzeit nicht möglich';
        } catch (error) { status.textContent = 'Update-Prüfung derzeit nicht möglich'; }
    }

    button.addEventListener('click', () => {
        if (!updateInfo) return;
        const setup = !updateInfo.can_start;
        dialog.querySelector('#update-dialog-description').textContent = setup ? 'Web-Updates einmalig aktivieren' : `Neue Version: v${updateInfo.latest_version}`;
        dialog.querySelector('#update-auth').hidden = setup;
        dialog.querySelector('#update-setup').hidden = !setup;
        dialog.querySelector('#update-confirm').hidden = setup;
        field.required = !setup;
        const savedKey = setup ? '' : readKey();
        field.value = savedKey;
        remember.checked = true;
        forget.hidden = !savedKey;
        keyStatus.textContent = savedKey ? 'Gespeicherter Schlüssel wurde geladen.' : 'Noch kein Schlüssel in diesem Browser gespeichert.';
        dialog.showModal();
    });
    dialog.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        if (!updateInfo?.can_start) return;
        const confirm = dialog.querySelector('#update-confirm');
        confirm.disabled = true;
        try {
            const key = field.value.trim();
            // Persist before starting: the server can restart before its response arrives.
            if (remember.checked && !persistKey()) {
                error.textContent = 'Schlüssel konnte nicht gespeichert werden. Zum einmaligen Update „Schlüssel in diesem Browser merken“ abwählen.';
                return;
            }
            if (!remember.checked) forgetKey();
            const response = await fetch('/api/updates/start', {method:'POST', headers:{'X-Update-Key':key}, signal:AbortSignal.timeout(30000)});
            if (response.status === 403) {
                forgetKey(); field.value = ''; forget.hidden = true;
                keyStatus.textContent = 'Schlüssel abgelehnt und aus diesem Browser entfernt.';
            }
            const result = await response.json();
            if (!response.ok) throw new Error(result.detail || 'Update konnte nicht gestartet werden');
            dialog.close();
            showJob(result);
        } catch (failure) { error.textContent = failure.message; }
        finally { confirm.disabled = false; }
    });
    check();
    setInterval(check, 300000);
})();
