/* Keys are write-only; model output is always rendered as plain text. */
(() => {
    const byId = id => document.getElementById(id);
    async function api(path, body) {
        const response = await fetch(`/api/ai/${path}`, body === undefined ? {cache: 'no-store'} : {
            method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Eingaben prüfen oder erneut versuchen.');
        return data;
    }
    function settingsStatus(data) {
        for (const provider of ['openai', 'gemini']) {
            byId(`ai-${provider}-status`).textContent = data[provider].configured ? 'Schlüssel gespeichert' : 'Kein Schlüssel gespeichert';
            byId(`ai-${provider}-model`).value = data[provider].model;
            byId(`ai-${provider}-key`).value = '';
            byId(`ai-${provider}-delete`).checked = false;
        }
    }
    if (byId('ai-save')) {
        api('settings').then(settingsStatus).catch(error => {byId('ai-settings-result').textContent = error.message;});
        byId('ai-save').addEventListener('click', async () => {
            const button = byId('ai-save'); button.disabled = true;
            try {
                const body = {};
                for (const provider of ['openai', 'gemini']) body[provider] = {
                    key: byId(`ai-${provider}-key`).value.trim(), model: byId(`ai-${provider}-model`).value.trim(),
                    delete: byId(`ai-${provider}-delete`).checked
                };
                settingsStatus(await api('settings', body));
                byId('ai-settings-result').textContent = 'KI-Einstellungen gespeichert.';
            } catch (error) {byId('ai-settings-result').textContent = error.message;}
            finally {button.disabled = false;}
        });
    }
    if (!byId('ai-open')) return;
    let token = null, loading = false;
    const dialog = byId('ai-dialog');
    byId('ai-close').addEventListener('click', () => dialog.close());
    byId('ai-open').addEventListener('click', async () => {
        if (loading) return;
        token = null;
        byId('ai-send').disabled = true;
        byId('ai-result').textContent = '';
        byId('ai-result-status').textContent = '';
        byId('ai-preview').textContent = '';
        byId('ai-summary').textContent = 'Vorschau wird geladen …';
        dialog.showModal();
        try {
            const body = {};
            for (const name of ['level', 'host', 'app', 'search']) body[name] = byId(`filter-${name}`).value;
            const period = byId('filter-period').value;
            if (period !== 'ALL') body.hours = Number(period);
            body.exclude_hosts = Array.from(excludedHosts);
            const [preview, settings] = await Promise.all([api('preview', body), api('settings')]);
            token = preview.token;
            byId('ai-summary').textContent = `${preview.count} von ${preview.total} passenden Warnungen/Fehlern. Texte ggf. gekürzt; Vorschau 10 Minuten gültig.`;
            byId('ai-preview').textContent = preview.preview;
            for (const option of byId('ai-provider').options) {
                const cfg = settings[option.value];
                option.disabled = !cfg.configured || !cfg.model;
                option.textContent = `${option.value === 'openai' ? 'OpenAI' : 'Gemini'}${option.disabled ? ' – bitte einrichten' : ` · ${cfg.model}`}`;
            }
            const available = Array.from(byId('ai-provider').options).find(option => !option.disabled);
            if (available) byId('ai-provider').value = available.value;
            else byId('ai-result-status').textContent = 'Bitte zuerst unter Einstellungen → KI-Analyse einen API-Schlüssel und eine Modell-ID speichern.';
            byId('ai-send').disabled = !token || !available;
        } catch (error) {byId('ai-summary').textContent = error.message;}
    });
    byId('ai-send').addEventListener('click', async () => {
        if (loading || !token) return;
        loading = true; byId('ai-send').disabled = true;
        byId('ai-provider').disabled = true;
        byId('ai-result').textContent = '';
        byId('ai-result-status').textContent = 'Analyse läuft …';
        try {
            const data = await api('analyze', {provider: byId('ai-provider').value, token});
            byId('ai-result-status').textContent = `${data.provider} · ${data.model} · ${data.count} Meldungen${data.incomplete ? ' · Antwort wurde wegen des Ausgabelimits gekürzt.' : ''}`;
            byId('ai-result').textContent = data.analysis;
        } catch (error) {byId('ai-result-status').textContent = error.message;}
        finally {loading = false; byId('ai-send').disabled = false; byId('ai-provider').disabled = false;}
    });
})();
