const {copyFileSync, mkdirSync} = require('node:fs');
mkdirSync('static/vendor', {recursive:true});
copyFileSync('node_modules/lucide/dist/umd/lucide.min.js','static/vendor/lucide.min.js');
copyFileSync('node_modules/lucide/LICENSE','static/vendor/LUCIDE-LICENSE');
copyFileSync('node_modules/tailwindcss/LICENSE','static/vendor/TAILWIND-LICENSE');
