import {build} from 'esbuild';
await build({entryPoints:{app:'client/app.js','editor.worker':'node_modules/monaco-editor/esm/vs/editor/editor.worker.js'},bundle:true,outdir:'static/dist',format:'esm',loader:{'.ttf':'file'},minify:true,sourcemap:false});
