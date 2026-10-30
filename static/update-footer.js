(() => {
    const status = document.getElementById('footer-update-status');
    const button = document.getElementById('footer-update-button');
    if (!status || !button) return;
    let updateInfo = null;
    let polling = false;
    let waiting = sessionStorage.getItem('rsyslog-update-pending') === '1';
    const dialog = document.createElement('dialog');
    dialog.style.cssText = 'width:min(92vw,440px);border:1px solid #334155;border-radius:16px;padding:24px;background:#0f172a;color:#e2e8f0';
    dialog.innerHTML = `<form method="dialog"><h2 style="font-size:18px;font-weight:600">App aktualisieren</h2>
        <p id="update-dialog-description" style="font-size:14px;color:#94a3b8;margin:12px 0"></p>
        <p style="font-size:13px;color:#94a3b8">Die App wird kurz neu gestartet. Logs und Einstellungen werden gesichert.</p>
        <label for="web-update-key" style="display:block;margin-top:16px;font-size:14px">Update-Schlüssel</label>
        <input id="web-update-key" type="password" autocomplete="off" required style="width:100%;box-sizing:border-box;margin:8px 0;padding:10px;background:#020617;color:white;border:1px solid #475569;border-radius:8px">
        <p id="update-dialog-error" role="alert" style="font-size:13px;color:#fda4af"></p>
        <div style="display:flex;justify-content:flex-end;gap:12px;margin-top:16px">
            <button type="button" id="update-cancel">Abbrechen</button>
            <button type="submit" id="update-confirm" style="padding:8px 12px;border-radius:8px;background:#0284c7;color:white">Update starten</button>
        </div></form>`;
    document.body.append(dialog);
    const field = dialog.querySelector('#web-update-key');
    const error = dialog.querySelector('#update-dialog-error');
    dialog.querySelector('#update-cancel').addEventListener('click', () => dialog.close());
    dialog.addEventListener('close', () => { field.value = ''; error.textContent = ''; });

    function showJob(job) {
        if (job.state === 'queued' || job.state === 'running') {
            waiting = true;
            sessionStorage.setItem('rsyslog-update-pending', '1');
            status.textContent = job.state === 'queued' ? 'Update wird gestartet …' : 'Update läuft …';
            button.hidden = true;
            poll();
            return true;
        }
        if (job.state === 'failed') {
            waiting = false;
            sessionStorage.removeItem('rsyslog-update-pending');
            status.textContent = 'Update fehlgeschlagen · Serverstatus prüfen';
            status.title = job.message || '';
            return false;
        }
        if (job.state === 'succeeded' && waiting) {
            waiting = false;
            sessionStorage.removeItem('rsyslog-update-pending');
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
            button.hidden = !updateInfo.available || !updateInfo.can_start;
            if (updateInfo.job.state === 'failed') return;
            status.title = '';
            if (updateInfo.available) {
                status.textContent = `Update auf v${updateInfo.latest_version} verfügbar`;
                if (!updateInfo.can_start) status.title = 'Web-Updates einmalig mit dem Debian-Installer oder Updater einrichten.';
            } else if (updateInfo.check_state === 'current') status.textContent = 'Auf aktuellem Stand';
            else if (updateInfo.check_state === 'manual') status.textContent = 'Git-Stand manuell prüfen';
            else status.textContent = 'Update-Prüfung derzeit nicht möglich';
        } catch (error) { status.textContent = 'Update-Prüfung derzeit nicht möglich'; }
    }

    button.addEventListener('click', () => {
        dialog.querySelector('#update-dialog-description').textContent = `Neue Version: v${updateInfo.latest_version}`;
        dialog.showModal();
    });
    dialog.querySelector('form').addEventListener('submit', async event => {
        event.preventDefault();
        const confirm = dialog.querySelector('#update-confirm');
        confirm.disabled = true;
        try {
            const response = await fetch('/api/updates/start', {method:'POST', headers:{'X-Update-Key':field.value}, signal:AbortSignal.timeout(30000)});
            field.value = '';
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
