const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const elements={};
function element(id){return elements[id] ||= {value:'',disabled:false,textContent:'',events:{},addEventListener(name,cb){this.events[name]=cb;},showModal(){this.open=true;},close(){this.open=false;}};}
const calls=[],events=[];
const ctx=vm.createContext({document:{getElementById:element},window:{dispatchEvent:event=>events.push(event)},CustomEvent:class{constructor(type,options){this.type=type;this.detail=options.detail;}},encodeURIComponent,JSON,
    copyText:async()=>true,fetch:async(url,options)=>{calls.push({url,options});return {ok:true,json:async()=>url.includes('removal?')?{logs:3,incidents:1,host_rules:0,ai_cache:2,uninstall_command:'curl example | bash'}:{host:'192.0.2.1',success:true}};}});
vm.runInContext(fs.readFileSync('static/host-removal.js','utf8'),ctx);
(async()=>{
    await ctx.window.openHostRemoval({host:'192.0.2.1',display_host:'Testgerät'});
    assert.equal(element('remove-host-submit').disabled,true);
    await element('remove-host-copy').events.click();assert.equal(calls.length,1);
    element('remove-host-confirm').value='wrong';element('remove-host-confirm').events.input();
    await element('remove-host-submit').events.click();assert.equal(calls.length,1);
    element('remove-host-confirm').value='192.0.2.1';element('remove-host-confirm').events.input();
    assert.equal(element('remove-host-submit').disabled,false);
    await element('remove-host-submit').events.click();
    assert.equal(calls.length,2);assert.equal(calls[1].options.headers['X-RSyslog-Action'],'remove-host');
    assert.equal(events[0].detail.host,'192.0.2.1');assert.equal(element('remove-host-dialog').open,false);
    console.log('Entfernen-Dialog: Kopieren ohne Löschung, exakte IP-Bestätigung und Schutzheader OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
