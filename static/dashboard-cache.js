/* One bounded, last-successful snapshot per origin; never a replacement for live refresh. */
(() => {
    const KEY = 'rsyslog-dashboard-v1';
    const MAX_BYTES = 512 * 1024;
    const MAX_AGE_MS = 24 * 60 * 60 * 1000;
    const counts = ['known_hosts','recent_hosts','logs_24h','error_only_24h','critical_24h','warnings_24h','open_incidents','heartbeat_hosts','offline_hosts'];
    const number = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
    function valid(data) {
        return data && counts.every(key => number(data[key])) && number(data.measured_at)
            && data.ingestion && ['logs_last_minute','logs_per_minute_5m'].every(key => number(data.ingestion[key]))
            && data.storage_policy && typeof data.storage_policy.message === 'string'
            && ['hosts','latest','attention'].every(key => Array.isArray(data[key]))
            && [data.hosts, data.unassigned_hosts || []].every(hosts => Array.isArray(hosts) && hosts.every(host => host && typeof host.host === 'string' && ['online','offline','syslog'].includes(host.status) && number(host.log_count) && number(host.errors_24h)))
            && [...data.latest,...data.attention].every(log => log && ['host','app','level','message','timestamp'].every(key => typeof log[key] === 'string'));
    }
    function load(storage, now = Date.now()) {
        try {
            const text = storage.getItem(KEY);
            if (!text || text.length * 2 > MAX_BYTES) return null;
            const entry = JSON.parse(text);
            if (entry.schema !== 1 || !number(entry.savedAt) || entry.savedAt > now + 60000 || now - entry.savedAt > MAX_AGE_MS || !valid(entry.data)) return null;
            return entry.data;
        } catch (_) { return null; }
    }
    function save(storage, data, now = Date.now()) {
        if (!valid(data)) return false;
        try {
            const text = JSON.stringify({schema:1,savedAt:now,data});
            if (text.length * 2 > MAX_BYTES) { storage.removeItem(KEY); return false; }
            storage.setItem(KEY, text); return true;
        } catch (_) { return false; }
    }
    function clear(storage) { try { storage.removeItem(KEY); } catch (_) {} }
    window.DashboardCache = {load,save,clear,valid};
})();
