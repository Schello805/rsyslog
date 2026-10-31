(() => {
    const el=id=>document.getElementById(id);
    let page=1, loading=false, refresh;
    async function load() {
        if(loading || location.hash!=='#incidents')return;
        loading=true;
        try {
            const response=await fetch('/api/incidents?'+new URLSearchParams({state:el('incident-state').value,search:el('incident-search').value,page}),{cache:'no-store'});
            if(!response.ok)throw Error('Vorfälle konnten nicht geladen werden.');
            const data=await response.json(), tbody=el('incident-rows'); tbody.replaceChildren();
            el('incident-result-status').textContent=`${data.total} passende Vorfälle · ${data.open_count} insgesamt offen · Seite ${data.page}`;
            el('incident-prev').disabled=page<=1; el('incident-next').disabled=page*data.per_page>=data.total;
            if(!data.incidents.length){const row=document.createElement('tr'),td=document.createElement('td');td.colSpan=4;td.textContent='Keine passenden Vorfälle.';row.append(td);tbody.append(row);}
            for(const item of data.incidents){
                const row=document.createElement('tr');
                function cell(text){const td=document.createElement('td');td.textContent=text;row.append(td);return td;}
                const first=cell('');
                const heading=document.createElement('p');heading.className='font-semibold level-caption '+severityClass(item.level);heading.textContent=`${item.level} · ${item.display_host} · ${item.app}`;
                const message=document.createElement('p');message.className='text-xs mt-2';message.style.cssText='max-width:520px;white-space:pre-wrap;overflow-wrap:anywhere';message.textContent=item.message;first.append(heading,message);
                cell(item.occurrences.toLocaleString('de-DE'));
                cell(`${new Date(item.first_seen).toLocaleString('de-DE')} / ${new Date(item.last_seen).toLocaleString('de-DE')}`);
                const actions=cell(item.status==='open'?'Offen':'Erledigt'),button=document.createElement('button');button.type='button';button.className='block mt-2 text-sky-700';button.textContent=item.status==='open'?'Als erledigt markieren':'Wieder öffnen';
                button.addEventListener('click',async()=>{
                    button.disabled=true;
                    try{const res=await fetch('/api/incidents/'+encodeURIComponent(item.fingerprint),{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({status:item.status==='open'?'resolved':'open'})});if(!res.ok)throw Error('Status konnte nicht gespeichert werden.');await load();}
                    catch(error){el('incident-result-status').textContent=error.message;button.disabled=false;}
                });actions.append(button);tbody.append(row);
            }
        }catch(error){el('incident-result-status').textContent=error.message;}
        finally{loading=false;}
    }
    el('incident-search').addEventListener('input',()=>{clearTimeout(refresh);page=1;refresh=setTimeout(load,250);});
    el('incident-state').addEventListener('change',()=>{page=1;load();});
    el('incident-prev').addEventListener('click',()=>{page=Math.max(1,page-1);load();});
    el('incident-next').addEventListener('click',()=>{page++;load();});
    window.addEventListener('hashchange',load);load();
    setInterval(()=>{if(!document.hidden)load();},10000);
})();
