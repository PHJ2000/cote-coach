import {build} from 'esbuild';
import {copyFile} from 'node:fs/promises';
await build({entryPoints:{recovery:'client/recovery.js',app:'client/app.js','theme-init':'client/theme-init.js','editor.worker':'node_modules/monaco-editor/esm/vs/editor/editor.worker.js'},bundle:true,outdir:'static/dist',format:'esm',loader:{'.ttf':'file'},minify:true,sourcemap:false});

await copyFile('static/recovery.html','static/dist/recovery.html');
