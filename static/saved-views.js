/* Saved log workflows stay in this browser; no credentials are stored. */
(() => {
    const key = 'rsyslog-log-views-v1';
    const fields = ['search', 'level', 'host', 'app', 'period', 'limit'];
    function validate(value) {
        if (!Array.isArray(value)) return [];
        return value.slice(0, 20).filter(v => v && typeof v.name === 'string' && v.name.trim() && v.name.length <= 80 && v.filters && fields.every(f => typeof v.filters[f] === 'string' && v.filters[f].length <= 2000) && Array.isArray(v.excluded) && v.excluded.length <= 200 && v.excluded.every(h => typeof h === 'string' && h.length <= 255) && ['timestamp','level','host','app','message'].includes(v.sortBy) && ['asc','desc'].includes(v.sortOrder));
    }
    function read(storage) { return validate(JSON.parse(storage.getItem(key) || '[]')); }
    function write(storage, views) { storage.setItem(key, JSON.stringify(validate(views))); }
    function init({capture, apply}) {
        const select = document.getElementById('saved-view-select');
        const status = document.getElementById('saved-view-status');
        let views = [];
        function render() {
            select.replaceChildren(new Option('Gespeicherte Ansicht wählen …', ''));
            views.forEach((view, index) => select.add(new Option(view.name, String(index))));
        }
        try { views = read(localStorage); } catch (_) { status.textContent = 'Gespeicherte Ansichten konnten nicht geladen werden.'; }
        render();
        select.addEventListener('change', () => {
            const view = views[select.value];
            if (select.value !== '' && view) { document.getElementById('saved-view-name').value = view.name; apply(view); status.textContent = `Ansicht „${view.name}“ geladen.`; }
        });
        document.getElementById('saved-view-save').addEventListener('click', () => {
            const name = document.getElementById('saved-view-name').value.trim();
            if (!name) { status.textContent = 'Bitte einen Namen für die Ansicht eingeben.'; document.getElementById('saved-view-name').focus(); return; }
            if (name.length > 80) { status.textContent = 'Bitte höchstens 80 Zeichen verwenden.'; return; }
            const index = views.findIndex(view => view.name === name);
            if (index >= 0 && select.value !== String(index)) { status.textContent = 'Name bereits vergeben. Zum Ändern zuerst diese Ansicht auswählen oder einen anderen Namen verwenden.'; return; }
            const next = views.slice();
            if (index >= 0) next[index] = {name, ...capture()}; else next.push({name, ...capture()});
            if (next.length > 20) { status.textContent = 'Maximal 20 Ansichten. Bitte zuerst eine löschen.'; return; }
            try { write(localStorage, next); views = next; render(); select.value = String(views.findIndex(view => view.name === name)); status.textContent = `Ansicht „${name}“ gespeichert (in diesem Browser).`; }
            catch (_) { status.textContent = 'Speichern nicht möglich. Browserspeicher ist nicht verfügbar oder voll.'; }
        });
        document.getElementById('saved-view-delete').addEventListener('click', () => {
            if (select.value === '' || !views[select.value]) return;
            const view = views[select.value];
            if (!confirm(`Ansicht „${view.name}“ löschen?`)) return;
            const next = views.filter(item => item !== view);
            try { write(localStorage, next); views = next; render(); status.textContent = 'Ansicht gelöscht.'; }
            catch (_) { status.textContent = 'Löschen nicht möglich: Browserspeicher ist gesperrt.'; }
        });
    }
    window.LogViews = {init, validate, read, write};
})();
