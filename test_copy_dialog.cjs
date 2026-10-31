const fs = require('node:fs'), vm = require('node:vm'), assert = require('node:assert/strict');
const source = fs.readFileSync('templates/index.html','utf8');
const code = source.slice(source.indexOf('        async function copyText('), source.indexOf('        function copyModalMessage('));
let parent, copied, restored = false;
const dialog = {appendChild(field){parent='dialog';}}, body = {appendChild(field){parent='body';}};
const context = {
    window:{isSecureContext:false,getSelection:()=>null}, navigator:{},
    document:{activeElement:{focus(){restored=true;}},querySelector:()=>dialog,body,
        createElement:()=>({style:{},setAttribute(){},focus(){},select(){},setSelectionRange(){},remove(){}}),
        execCommand:command=>{copied=command;return parent==='dialog';}},showToast(){}
};
vm.runInNewContext(code,context);
(async()=> {
    assert.equal(await context.copyText('Meldungen für manuelle Analyse'),true);
    assert.equal(parent,'dialog');assert.equal(copied,'copy');assert.equal(restored,true);
    console.log('HTTP-Kopierfallback innerhalb des geöffneten Dialogs: OK');
})().catch(error=>{console.error(error);process.exitCode=1;});
