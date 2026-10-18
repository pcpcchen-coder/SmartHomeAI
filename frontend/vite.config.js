import { defineConfig } from 'vite';
export default defineConfig({base:'/static/',server:{port:5173,proxy:{'/api':'http://127.0.0.1:8788','/assets/home':'http://127.0.0.1:8788'}},build:{outDir:'../web',emptyOutDir:true}});
