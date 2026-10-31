const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const moduleCode = fs.readFileSync('static/dashboard-cache.js','utf8');
const html = fs.readFileSync('templates/index.html','utf8');
const uiCode = html.slice(html.indexOf('        let dashboardRequestActive = false;'),html.indexOf('        function toggleLegacyHostOptions()'));
let saved = null;
const storage = {getItem:()=>saved,setItem:(_,value)=>{saved=value;},removeItem:()=>{saved=null;}};
const base = {known_hosts:3,recent_hosts:2,logs_24h:100,error_only_24h:1,critical_24h:2,warnings_24h:3,open_incidents:2,heartbeat_hosts:1,offline_hosts:0,measured_at:Date.now()/1000,
    ingestion:{logs_last_minute:5,logs_per_minute_5m:4},storage_policy:{warning:false,message:''},hosts:[],attention:[],latest:[],unassigned_hosts:[]};
function setup(store=storage) {
    const elements = {};
    const ctx = vm.createContext({window:{localStorage:store},Date,JSON,Number,Array,AbortController,setTimeout,clearTimeout,
        activeWorkflow:()=> 'overview',document:{hidden:false,getElementById:id=>elements[id] ||= {textContent:'',replaceChildren(){},append(){}}},
        renderOverviewEvents(){},renderDeviceTable(){},renderUnassignedHosts(){},deviceCard(){},fetch:async()=>({ok:true,json:async()=>base})});
    vm.runInContext(moduleCode,ctx); vm.runInContext(uiCode,ctx);
    return {ctx,elements,cache:ctx.window.DashboardCache};
}
(async()=> {
    const {ctx,elements,cache} = setup();ctx.DashboardCache=cache;
    assert.equal(cache.save(storage,base,100000),true);
    assert.equal(cache.load(storage,100001).known_hosts,3);
    assert.equal(cache.load(storage,100000+86400001),null);
    assert.equal(cache.load({getItem:()=>'{broken'}),null);
    assert.equal(cache.load({getItem(){throw Error('blocked');}}),null);
    assert.equal(cache.save({setItem(){throw Error('full');}},base),false);
    assert.equal(cache.save(storage,{...base,latest:[{host:'x',app:'a',level:'WARN',timestamp:'t',message:'x'.repeat(300000)}]}),false);
    assert.equal(saved,null);
    cache.save(storage,base);
    ctx.restoreDashboard();
    assert.equal(elements['overview-known'].textContent,3);
    assert.match(elements['dashboard-updated'].textContent,/Gespeicherter Stand/);
    let release;
    ctx.fetch = ()=>new Promise(resolve=>{release=resolve;});
    const pending = ctx.fetchStats();
    assert.equal(elements['overview-known'].textContent,3); // Existing view stays visible while request waits.
    release({ok:true,json:async()=>({...base,known_hosts:4})}); await pending;
    assert.equal(elements['overview-known'].textContent,4);
    assert.equal(cache.load(storage).known_hosts,4);
    ctx.fetch = async()=>({ok:false}); await ctx.fetchStats();
    assert.equal(elements['overview-known'].textContent,4);
    assert.match(elements['dashboard-updated'].textContent,/Aktualisierung fehlgeschlagen/);
    ctx.fetch = async()=>({ok:true,json:async()=>({broken:true})}); await ctx.fetchStats();
    assert.equal(elements['overview-known'].textContent,4);
    const blocked=setup(null); blocked.ctx.DashboardCache=blocked.cache;
    await blocked.ctx.fetchStats(); assert.equal(blocked.elements['overview-known'].textContent,3);
    assert.equal(blocked.cache.load(null),null);
    ctx.clearDashboardSnapshot(); assert.equal(cache.load(storage),null); assert.equal(elements['overview-known'].textContent,'…');
    console.log('Dashboard: Sofortiger alter Stand, Live-Ersatz, Fehlererhalt, Ablauf, Größenlimit und gesperrter Speicher OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
