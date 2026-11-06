const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements={},requests=[];
function el(id){return elements[id]??=( {value:'',hidden:false,disabled:false,textContent:'',options:[],handlers:{},addEventListener(type,fn){this.handlers[type]=fn;}});}
for(const id of ['ai-open','ai-setup','ai-close','ai-provider','ai-copy','ai-copy-status','ai-send','ai-result','ai-result-status','ai-preview','ai-summary','ai-empty-hint','ai-clear-level','ai-copy-open','ai-sample-limit'])el(id);
el('ai-provider').options=[{value:'gemini'}];el('ai-provider').value='gemini';el('ai-sample-limit').value='50';
for(const [key,value] of Object.entries({level:'INFO',host:'192.0.2.1',app:'systemd',search:'test',period:'24'}))el('filter-'+key).value=value;
const dialog=el('ai-dialog');dialog.open=false;dialog.showModal=()=>dialog.open=true;dialog.close=()=>dialog.open=false;
const settings={openai:{configured:false,model:''},gemini:{configured:true,model:'test'}};
let logRefreshes=0;
const ctx={document:{getElementById:id=>elements[id]||null},window:{addEventListener(){}},excludedHosts:new Set(['192.0.2.2']),fetchLogs:()=>logRefreshes++,copyText:async()=>true,console,fetch:async(url,options)=>{
 const body=options?.body?JSON.parse(options.body):undefined;requests.push({url,body});
 return {ok:true,json:async()=>url.endsWith('settings')?settings:{token:null,count:0,total:0,preview:'empty'}};
}};
vm.runInNewContext(fs.readFileSync('static/ai-analysis.js','utf8'),ctx);
(async()=>{
 await el('ai-open').handlers.click();
 assert.equal(el('ai-clear-level').hidden,false);assert.match(el('ai-empty-hint').textContent,/INFO/);assert.equal(el('ai-send').disabled,true);
 el('ai-clear-level').handlers.click();await new Promise(resolve=>setImmediate(resolve));
 const preview=requests.filter(r=>r.url.endsWith('preview')).at(-1).body;
 assert.equal(preview.level,'ALL');assert.equal(preview.host,'192.0.2.1');assert.equal(preview.app,'systemd');assert.equal(preview.search,'test');assert.equal(preview.hours,24);assert.deepEqual(preview.exclude_hosts,['192.0.2.2']);assert.equal(logRefreshes,1);
 assert.equal(el('ai-clear-level').hidden,true);assert.match(el('ai-empty-hint').textContent,/keine Warnungen/);
 assert.equal(requests.some(r=>r.url.endsWith('analyze')),false);
 console.log('Leere KI-Vorschau erklärt INFO-Filter; gezielte Korrektur erhält andere Filter und sendet keine Analyse OK');
})().catch(e=>{console.error(e);process.exitCode=1});
