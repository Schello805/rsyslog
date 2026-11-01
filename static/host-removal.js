/* The app only deletes its local data; client commands are copied, never executed remotely. */
(() => {
    const byId = id => document.getElementById(id);
    const dialog = byId('remove-host-dialog');
    if (!dialog) return;
    let current = null, command = '', ready = false, busy = false, generation = 0;
    const confirm = byId('remove-host-confirm'), submit = byId('remove-host-submit');
    function update() { submit.disabled = busy || !ready || confirm.value.trim() !== current?.host; }
    confirm.addEventListener('input', update);
    byId('remove-host-close').addEventListener('click', () => { if (!busy) dialog.close(); });
    dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    async function result(response) {
        let data;
        try { data = await response.json(); } catch (_) { throw Error('Serverantwort ungültig. Bitte erneut versuchen.'); }
        if (!response.ok) throw Error(typeof data.detail === 'string' ? data.detail : 'Gerät konnte nicht entfernt werden.');
        return data;
    }
    window.openHostRemoval = async host => {
        if (busy) return;
        const request = ++generation;
        current = host; ready = false; command = ''; confirm.value = ''; confirm.placeholder = host.host;
        byId('remove-host-identity').textContent = `${host.display_host || host.host} · ${host.host}`;
        byId('remove-host-command').textContent = 'Deinstallationsbefehl wird geladen …';
        byId('remove-host-scope').textContent = '';
        byId('remove-host-status').textContent = 'Löschumfang wird geladen …';
        byId('remove-host-copy').disabled = true; update(); dialog.showModal();
        try {
            const data = await result(await fetch(`/api/hosts/removal?host=${encodeURIComponent(host.host)}`, {cache:'no-store'}));
            if (request !== generation) return;
            command = data.uninstall_command;
            byId('remove-host-command').textContent = command || 'Serveradresse nicht ermittelbar. Deinstallationsskript mit --server ADRESSE aufrufen.';
            byId('remove-host-copy').disabled = !command;
            byId('remove-host-scope').textContent = `Gelöscht werden ${data.logs.toLocaleString('de-DE')} Logs, ${data.incidents} Vorfälle, ${data.host_rules} gerätespezifische Alarmregeln sowie Überwachung, Anzeigename und DNS-Cache dieses Geräts. Zusätzlich werden alle ${data.ai_cache} zwischengespeicherten KI-Analysen zurückgesetzt, da sie mehrere Geräte enthalten können.`;
            ready = true; byId('remove-host-status').textContent = ''; update();
        } catch (error) { byId('remove-host-status').textContent = error.message; }
    };
    byId('remove-host-copy').addEventListener('click', async () => {
        if (command) byId('remove-host-status').textContent = await copyText(command) ? 'Befehl kopiert. Auf dem betroffenen Client ausführen.' : 'Bitte den Befehl markieren und manuell kopieren.';
    });
    submit.addEventListener('click', async () => {
        if (busy || !ready || confirm.value.trim() !== current?.host) return;
        busy = true; update(); byId('remove-host-close').disabled = true;
        byId('remove-host-status').textContent = 'Gerät und Daten werden gelöscht …';
        try {
            const data = await result(await fetch('/api/hosts/remove', {
                method:'POST',headers:{'Content-Type':'application/json','X-RSyslog-Action':'remove-host'},
                body:JSON.stringify({host:current.host,confirm_host:confirm.value.trim()})
            }));
            window.dispatchEvent(new CustomEvent('rsyslog-host-removed',{detail:{host:data.host}}));
            dialog.close();
        } catch (error) { byId('remove-host-status').textContent = error.message; }
        finally { busy = false; byId('remove-host-close').disabled = false; update(); }
    });
})();
