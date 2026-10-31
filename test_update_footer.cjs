// Run with node test_update_footer.cjs. No browser, network or real credentials.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('static/update-footer.js', 'utf8');
function element() {
    return {value:'', hidden:false, checked:true, style:{}, listeners:{},
        addEventListener(name, callback) {this.listeners[name] = callback;},
        async fire(name) {return this.listeners[name]?.({preventDefault(){}});}};
}
async function load(storage, result = {status:200, body:{state:'queued'}}) {
    const elements = new Map();
    const get = id => {if (!elements.has(id)) elements.set(id, element()); return elements.get(id);};
    const dialog = element();
    dialog.querySelector = get;
    dialog.showModal = () => {dialog.open = true;};
    dialog.close = () => {dialog.open = false; dialog.fire('close');};
    let sentKey;
    const context = {
        document:{getElementById:get, createElement:()=>dialog, body:{append(){}}},
        localStorage:storage,
        sessionStorage:{getItem(){return null;},setItem(){},removeItem(){}},
        AbortSignal:{timeout(){}}, setTimeout(){}, setInterval(){}, location:{reload(){}},
        async fetch(url, options) {
            if (url === '/api/updates/start') {
                sentKey = options.headers['X-Update-Key'];
                if (result.beforeStart) result.beforeStart();
                if (result.error) throw new Error('offline');
                return {status:result.status,ok:result.status===200,json:async()=>result.body};
            }
            return {ok:true,json:async()=>url.endsWith('/status') ? {state:'queued'} : {
                available:true,can_start:true,latest_version:'1.0.99',job:{state:'idle'}}};
        }
    };
    vm.runInNewContext(source, context);
    await new Promise(resolve=>setImmediate(resolve));
    await get('footer-update-button').fire('click');
    return {get, dialog, key:()=>sentKey};
}
function memory() {
    const entries = new Map();
    return {getItem:key=>entries.get(key),setItem:(key,value)=>entries.set(key,value),removeItem:key=>entries.delete(key)};
}
(async () => {
    const name='rsyslog-update-key', storage=memory();
    let app=await load(storage);
    app.get('#web-update-key').value=' test-key ';
    await app.get('form').fire('submit');
    assert.equal(app.key(),'test-key');
    assert.equal(storage.getItem(name),'test-key');
    assert.equal(app.get('#web-update-key').value,'');
    app=await load(storage); // A later page/browser session retains the same local storage.
    assert.equal(app.get('#web-update-key').value,'test-key');
    assert.equal(app.get('#forget-update-key').hidden,false);
    await app.get('#forget-update-key').fire('click');
    assert.equal(storage.getItem(name),undefined);
    assert.equal(app.get('#web-update-key').value,'');
    app.get('#web-update-key').value='one-time';
    app.get('#remember-update-key').checked=false;
    await app.get('form').fire('submit');
    assert.equal(app.key(),'one-time');
    assert.equal(storage.getItem(name),undefined);
    storage.setItem(name,'old-key');
    app=await load(storage,{status:403,body:{detail:'Ungültiger Schlüssel'}});
    await app.get('form').fire('submit');
    assert.equal(storage.getItem(name),undefined);
    assert.equal(app.get('#web-update-key').value,'');
    assert.equal(app.get('#update-dialog-error').textContent,'Ungültiger Schlüssel');
    app=await load(storage,{status:500,body:{detail:'Serverfehler'}});
    app.get('#web-update-key').value='unverified';
    await app.get('form').fire('submit');
    assert.equal(storage.getItem(name),'unverified');
    assert.equal(app.get('#web-update-key').value,'unverified');
    storage.setItem(name,'saved');
    app=await load(storage,{error:true});
    await app.get('form').fire('submit');
    assert.equal(storage.getItem(name),'saved');
    assert.equal(app.get('#update-dialog-error').textContent,'offline');
    app.get('#remember-update-key').checked=false;
    await app.get('#remember-update-key').fire('change');
    assert.equal(storage.getItem(name),undefined);
    app=await load(storage);
    app.get('#web-update-key').value='saved-without-update';
    await app.get('#save-update-key').fire('click');
    assert.equal(storage.getItem(name),'saved-without-update');
    assert.match(app.get('#update-key-status').textContent,/gespeichert/);
    app=await load(storage,{error:true,beforeStart(){assert.equal(storage.getItem(name),'restart-key');}});
    app.get('#web-update-key').value='restart-key';
    await app.get('form').fire('submit');
    app=await load(storage);
    assert.equal(app.get('#web-update-key').value,'restart-key');
    const blocked={getItem(){throw Error();},setItem(){throw Error();},removeItem(){throw Error();}};
    app=await load(blocked);
    app.get('#web-update-key').value='one-time';
    await app.get('form').fire('submit');
    assert.equal(app.key(),undefined);
    assert.equal(app.dialog.open,true);
    assert.match(app.get('#update-key-status').textContent,/blockiert/);
    app.get('#remember-update-key').checked=false;
    await app.get('form').fire('submit');
    assert.equal(app.key(),'one-time');
    assert.equal(app.dialog.open,false);
    console.log('Update key: persistence, removal, opt-out, auth failure, retry and blocked storage OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
