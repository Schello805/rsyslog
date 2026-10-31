// No network, browser credentials or real updates. Run: node test_update_footer.cjs
const assert=require('node:assert/strict'), fs=require('node:fs'), vm=require('node:vm');
async function load({supported=true,result={status:202,body:{state:'queued'}},blocked=false}={}) {
 const elements=new Map();
 const get=id=>{if(!elements.has(id))elements.set(id,{style:{},listeners:{},addEventListener(n,f){this.listeners[n]=f;},async fire(n){return this.listeners[n]?.({preventDefault(){}});}});return elements.get(id);};
 const dialog=get('dialog');dialog.querySelector=get;dialog.showModal=()=>{dialog.open=true;};dialog.close=()=>{dialog.open=false;dialog.fire('close');};
 const requests=[];let removed=false;
 vm.runInNewContext(fs.readFileSync('static/update-footer.js','utf8'),{
  document:{getElementById:get,createElement:()=>dialog,body:{append(){}}},
  localStorage:{removeItem(){if(blocked)throw Error();removed=true;}},
  sessionStorage:{getItem(){if(blocked)throw Error();return null;},setItem(){if(blocked)throw Error();},removeItem(){if(blocked)throw Error();}},
  AbortSignal:{timeout(){}},setTimeout(){},setInterval(){},location:{reload(){}},
  async fetch(url,options){
   if(url==='/api/updates/start'){requests.push(options);return{ok:result.status===202,json:async()=>result.body};}
   return{ok:true,json:async()=>url.endsWith('/status')?{state:'queued'}:{available:true,can_start:supported,latest_version:'1.0.99',job:{state:'idle'}}};
  }
 });
 await new Promise(r=>setImmediate(r));await get('footer-update-button').fire('click');
 return{get,dialog,requests,removed};
}
(async()=>{
 let app=await load();assert.equal(app.removed,true);assert(!app.dialog.innerHTML.includes('web-update-key'));
 await app.get('form').fire('submit');assert.equal(app.requests.length,1);
 assert.deepEqual({...app.requests[0].headers},{'X-RSyslog-Update':'1'});assert.equal(app.dialog.open,false);
 app=await load({blocked:true});await app.get('form').fire('submit');assert.equal(app.requests.length,1);
 app=await load({supported:false});await app.get('form').fire('submit');assert.equal(app.requests.length,0);
 app=await load({result:{status:409,body:{detail:'Update läuft bereits'}}});await app.get('form').fire('submit');
 assert.equal(app.get('#update-dialog-error').textContent,'Update läuft bereits');assert.equal(app.dialog.open,true);
 console.log('Keyless update: confirmation, header, old-key cleanup, blocked storage, unsupported setup and errors OK');
})().catch(e=>{console.error(e);process.exitCode=1;});
