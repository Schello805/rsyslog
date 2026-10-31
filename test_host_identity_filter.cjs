const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const html = fs.readFileSync('templates/index.html','utf8');
const code = html.slice(html.indexOf('        function toggleLegacyHostOptions()'),html.indexOf('        function resetFilters()'));
class Option {constructor(label,value){this.label=label;this.value=value;}}
const select = {value:'ALL',children:[],replaceChildren(...items){this.children=items;this.value='ALL';},append(item){this.children.push(item);},add(item){this.children.push(item);}};
const toggle = {checked:false};
const app = {value:'ALL',innerHTML:'',add(){}};
const data = {hosts:['192.0.2.22','CCU3'],host_labels:{'192.0.2.22':'CCU3'},apps:['app'],host_groups:[
    {kind:'source_ip',label:'Geräte nach Quell-IP',options:[{value:'192.0.2.22',label:'192.0.2.22 (CCU3)'}]},
    {kind:'unassigned',label:'Altbestand',options:[{value:'CCU3',label:'CCU3 · Quell-IP nicht gespeichert'}]}
]};
const elements = {'filter-host':select,'show-legacy-hosts':toggle,'filter-app':app};
const ctx = {Option,JSON,Set,console,hostLabels:{},availableExcludeHosts:[],legacyHostValues:new Set(),
    document:{hidden:false,getElementById:id=>elements[id],createElement:()=>({children:[],append(item){this.children.push(item);}})},
    activeWorkflow:()=> 'logs',fetch:async()=>({json:async()=>data}),hostLabel:host=>host,escapeHtml:value=>value,
    renderExcludedHosts(){},updateHostNameButton(){},fetchLogs(){}};
vm.runInNewContext(code,ctx);
(async()=> {
    await ctx.fetchFilters();
    assert.equal(select.children.length,2); // All + IP group only.
    assert.equal(select.children[1].children[0].value,'192.0.2.22');
    assert.equal(JSON.stringify(ctx.availableExcludeHosts),JSON.stringify(['192.0.2.22']));
    toggle.checked=true; await ctx.fetchFilters(); assert.equal(select.children.length,3);
    select.value='CCU3'; toggle.checked=false; ctx.toggleLegacyHostOptions();
    assert.equal(select.value,'ALL'); await ctx.fetchFilters(); assert.equal(select.children.length,2);
    select.value='CCU3'; await ctx.fetchFilters();
    assert.equal(toggle.checked,true); assert.equal(select.value,'CCU3'); // Explicit historic link/view stays usable.
    console.log('Hostfilter: nur IP-Geräte standardmäßig, Altbestand auf Wunsch, historische Direktwahl erhalten OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
