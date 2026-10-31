"""Persistent groups of repeated warnings/errors with explicit resolution state."""
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

LEVELS = ('WARN','WARNING','ERROR','CRITICAL','ALERT','EMERGENCY')
router = APIRouter(prefix='/api/incidents')


def record(conn, entry):
    if entry['level'] not in LEVELS:
        return
    normalized = re.sub(r'\s+', ' ', entry['message']).strip()
    fingerprint = hashlib.sha256(json.dumps([entry['host'],entry['app'],entry['level'],normalized],ensure_ascii=False).encode()).hexdigest()
    seen = entry.get('received_at') or entry['timestamp']
    conn.execute('''INSERT INTO incidents(fingerprint,host,app,level,message,first_seen,last_seen,occurrences,status)
        VALUES(?,?,?,?,?,?,?,?, 'open') ON CONFLICT(fingerprint) DO UPDATE SET
        first_seen=MIN(first_seen,excluded.first_seen), last_seen=MAX(last_seen,excluded.last_seen),
        occurrences=occurrences+excluded.occurrences, status='open', resolved_at=NULL''',
        (fingerprint,entry['host'],entry['app'],entry['level'],entry['message'][:4000],seen,seen,entry.get('occurrences',1)))


def initialize(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS incidents(
            fingerprint TEXT PRIMARY KEY,host TEXT NOT NULL,app TEXT NOT NULL,level TEXT NOT NULL,
            message TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,
            occurrences INTEGER NOT NULL,status TEXT NOT NULL,resolved_at TEXT)''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_incidents_status ON incidents(status,last_seen)')
    if conn.execute("SELECT 1 FROM settings WHERE key='migration_incidents_v1'").fetchone():
        return
    progress = conn.execute("SELECT value FROM settings WHERE key='migration_incidents_cursor'").fetchone()
    last_id = int(progress[0]) if progress else 0
    while True:
        rows = conn.execute("SELECT id,host,app,level,message,timestamp,received_at FROM logs WHERE id>? AND level IN ('WARN','WARNING','ERROR','CRITICAL','ALERT','EMERGENCY') ORDER BY id LIMIT 1000",(last_id,)).fetchall()
        if not rows: break
        with conn:
            for row in rows:
                record(conn,dict(row)); last_id=row['id']
            conn.execute("INSERT INTO settings(key,value) VALUES('migration_incidents_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(last_id),))
    with conn:
        conn.execute("INSERT INTO settings(key,value) VALUES('migration_incidents_v1','done')")
        conn.execute("DELETE FROM settings WHERE key='migration_incidents_cursor'")


def open_count():
    from database import get_db_connection
    return get_db_connection().execute("SELECT COUNT(*) FROM incidents WHERE status='open'").fetchone()[0]


@router.get('')
async def listing(state:Literal['open','resolved','all']='open', search:str=Query('',max_length=1000), page:int=Query(1,ge=1), per_page:int=Query(50,ge=1,le=200)):
    from database import get_db_connection, get_host_aliases
    conn=get_db_connection(); clauses=[]; params=[]
    if state!='all': clauses.append('status=?'); params.append(state)
    if search.strip():
        clauses.append('(host LIKE ? OR app LIKE ? OR message LIKE ? OR EXISTS(SELECT 1 FROM host_aliases WHERE host_aliases.host=incidents.host AND name LIKE ?))')
        params.extend(['%'+search.strip()+'%']*4)
    where=' WHERE '+' AND '.join(clauses) if clauses else ''
    total=conn.execute('SELECT COUNT(*) FROM incidents'+where,params).fetchone()[0]
    rows=[dict(row) for row in conn.execute('SELECT * FROM incidents'+where+' ORDER BY last_seen DESC LIMIT ? OFFSET ?',params+[per_page,(page-1)*per_page])]
    aliases=get_host_aliases()
    for row in rows: row['display_host']=aliases.get(row['host'],row['host'])
    return {'incidents':rows,'total':total,'page':page,'per_page':per_page,'open_count':open_count()}


class State(BaseModel):
    status:Literal['open','resolved']


@router.post('/{fingerprint}')
async def change(fingerprint:str, body:State, request:Request):
    from database import get_db_connection
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'Vorfälle bitte aus derselben App bearbeiten.')
    conn=get_db_connection()
    with conn:
        result=conn.execute('UPDATE incidents SET status=?,resolved_at=? WHERE fingerprint=?',
            (body.status,datetime.now(timezone.utc).isoformat() if body.status=='resolved' else None,fingerprint))
    if not result.rowcount: raise HTTPException(404,'Vorfall nicht gefunden.')
    return {'success':True}
