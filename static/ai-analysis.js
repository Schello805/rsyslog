/* Keys are write-only; model output is always rendered as plain text. */
(() => {
    const byId = id => document.getElementById(id);
    async function api(path, body, signal) {
        const response = await fetch(`/api/ai/${path}`, body === undefined ? {cache: 'no-store', signal} : {
            method: 'POST', signal, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)
        });
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Eingaben prüfen oder erneut versuchen.');
        return data;
    }
    function settingsStatus(data) {
        for (const provider of ['openai', 'gemini']) {
            byId(`ai-${provider}-status`).textContent = data[provider].configured ? 'Schlüssel gespeichert' : 'Kein Schlüssel gespeichert';
            byId(`ai-${provider}-model`).value = data[provider].model;
            const keyField = byId(`ai-${provider}-key`);
            keyField.value = '';
            // A placeholder signals saved credentials without submitting a dummy key.
            keyField.placeholder = data[provider].configured ? '********' : 'Neuen Schlüssel eingeben';
            keyField.setAttribute('aria-describedby', `ai-${provider}-status`);
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
    function updateSetupLink(settings) {
        const ready = ['openai', 'gemini'].some(provider => settings[provider]?.configured && settings[provider]?.model);
        byId('ai-setup').hidden = ready;
    }
    async function refreshSetupLink() {
        try { updateSetupLink(await api('settings')); }
        catch { byId('ai-setup').hidden = false; }
    }
    refreshSetupLink();
    window.addEventListener('focus', refreshSetupLink);
    window.addEventListener('pageshow', event => { if (event.persisted) refreshSetupLink(); });
    let token = null, loading = false, hasCachedAnalysis = false;
    const dialog = byId('ai-dialog');
    byId('ai-close').addEventListener('click', () => dialog.close());
    function renderAnalysis(data) {
        const when = new Date(data.created_at * 1000).toLocaleString('de-DE');
        byId('ai-result-status').textContent = `${data.cached ? 'Gespeicherte' : 'Neue'} Analyse · ${when} · ${data.provider} · ${data.model} · ${data.count} Meldungen${data.incomplete ? ' · Antwort gekürzt.' : ''}${data.cache_saved === false ? ' · Nicht gespeichert; Speicher prüfen oder Antwort unvollständig.' : ''}`;
        byId('ai-result').textContent = data.analysis;
        hasCachedAnalysis = data.cache_saved;
        byId('ai-send').textContent = hasCachedAnalysis ? 'Erneut analysieren (neuer API-Aufruf)' : 'Vorschau senden & analysieren';
    }
    async function loadCached() {
        const currentToken = token, provider = byId('ai-provider').value;
        hasCachedAnalysis = false;
        byId('ai-result').textContent = ''; byId('ai-result-status').textContent = '';
        byId('ai-send').textContent = 'Vorschau senden & analysieren';
        if (!currentToken || loading) return;
        try {
            const data = await api('cached', {provider, token: currentToken});
            if (token !== currentToken || provider !== byId('ai-provider').value || loading) return;
            if (data.cached) renderAnalysis(data);
        } catch (error) { byId('ai-result-status').textContent = error.message; }
    }
    byId('ai-provider').addEventListener('change', loadCached);

    byId('ai-open').addEventListener('click', async () => {
        if (loading) return;
        token = null; hasCachedAnalysis = false;
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
            updateSetupLink(settings);
            token = preview.token;
            byId('ai-summary').textContent = `${preview.count} von ${preview.total} passenden Warnungen/Fehlern, ${preview.groups || preview.count} unterschiedliche Meldungen. Wiederholungen zusammengefasst, Texte ggf. gekürzt. Vorschau 10 Minuten gültig.`;
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
            if (token && available) await loadCached();
        } catch (error) {byId('ai-summary').textContent = error.message;}
    });
    byId('ai-send').addEventListener('click', async () => {
        if (loading || !token) return;
        loading = true; byId('ai-send').disabled = true;
        byId('ai-provider').disabled = true;
        byId('ai-result').textContent = '';
        const started = Date.now();
        const progress = () => {byId('ai-result-status').textContent = `Analyse läuft seit ${Math.floor((Date.now() - started) / 1000)} Sekunden · warte auf Anbieter (max. 45 Sekunden) …`;};
        progress();
        const timer = setInterval(progress, 1000);
        try {
            const data = await api('analyze', {provider: byId('ai-provider').value, token, refresh: Boolean(hasCachedAnalysis)}, AbortSignal.timeout(55000));
            renderAnalysis(data);
        } catch (error) {byId('ai-result-status').textContent = ['TimeoutError', 'AbortError'].includes(error.name) ? 'Zeitüberschreitung. Verbindung zum Server prüfen und Analyse erneut starten.' : error.message;}
        finally {clearInterval(timer); loading = false; byId('ai-send').disabled = false; byId('ai-provider').disabled = false;}
    });
})();
