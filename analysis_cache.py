"""Small persistent cache of analyses; no API credentials or raw payloads stored."""
import hashlib
import json
import time

TTL = 7 * 86400
MAX_ENTRIES = 20


def initialize(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS analysis_cache(
            cache_key TEXT PRIMARY KEY,provider TEXT NOT NULL,model TEXT NOT NULL,
            analysis TEXT NOT NULL,log_count INTEGER NOT NULL,created_at REAL NOT NULL)''')


def key(provider, model, credential, instructions, payload):
    credential_hash = hashlib.sha256(credential.encode()).hexdigest()
    return hashlib.sha256(json.dumps([provider,model,credential_hash,instructions,payload],ensure_ascii=False).encode()).hexdigest()


def get(conn, cache_key, current_count):
    row=conn.execute('SELECT * FROM analysis_cache WHERE cache_key=? AND created_at>?',(cache_key,time.time()-TTL)).fetchone()
    if not row:return None
    return {'analysis':row['analysis'],'provider':row['provider'],'model':row['model'],
            'count':row['log_count'],'current_count':current_count,'created_at':row['created_at'],
            'cached':True,'incomplete':False,'cache_saved':True}


def cleanup(conn):
    with conn:
        conn.execute('DELETE FROM analysis_cache WHERE created_at<?',(time.time()-TTL,))
        conn.execute('DELETE FROM analysis_cache WHERE cache_key NOT IN (SELECT cache_key FROM analysis_cache ORDER BY created_at DESC LIMIT ?)',(MAX_ENTRIES,))


def save(conn, cache_key, result):
    with conn:
        conn.execute('INSERT OR REPLACE INTO analysis_cache VALUES(?,?,?,?,?,?)',
                     (cache_key,result['provider'],result['model'],result['analysis'],result['count'],result['created_at']))
        conn.execute('DELETE FROM analysis_cache WHERE created_at<?',(time.time()-TTL,))
        conn.execute('DELETE FROM analysis_cache WHERE cache_key NOT IN (SELECT cache_key FROM analysis_cache ORDER BY created_at DESC LIMIT ?)',(MAX_ENTRIES,))
