// Exercise transport failure paths, including a body that stalls after headers.
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

(async()=>{
    const handlers={}, entries=new Map(), buckets=new Map();
    const bucket={match:async key=>entries.get(key)?.clone(),put:async(key,value)=>entries.set(key,value.clone())};
    buckets.set('autoxak-shell-v3',bucket);
    buckets.set('autoxak-shell-v2',{});
    buckets.set('unrelated-app',{});
    let request;
    const sandbox={URL,Response,Headers,AbortController,console,clearTimeout,
        setTimeout:callback=>setTimeout(callback,25),
        fetch:(...args)=>request(...args),
        caches:{open:async()=>bucket,keys:async()=>[...buckets.keys()],delete:async key=>buckets.delete(key)},
        self:{location:{origin:'https://autoxak.example'},clients:{claim:async()=>{}},addEventListener:(name,handler)=>{handlers[name]=handler;}}};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../../static/sw.js'),'utf8'),sandbox);
    async function load(url='https://autoxak.example/?probe=1',method='GET') {
        let result;
        handlers.fetch({request:{url,method},respondWith:promise=>{result=promise;}});
        return result;
    }
    entries.set('/',new Response('cached shell'));
    request=async()=>new Response('fresh shell');
    assert.equal(await (await load()).text(),'fresh shell');
    assert.equal(await (await bucket.match('/')).text(),'fresh shell');
    assert.equal(entries.size,1);
    request=async()=>new Response('decoded shell',{headers:{'Content-Encoding':'gzip','Content-Length':'4'}});
    const decoded=await load();
    assert.equal(await decoded.text(),'decoded shell');
    assert.equal(decoded.headers.get('content-encoding'),null);
    assert.equal(decoded.headers.get('content-length'),null);
    entries.set('/',new Response('fresh shell'));
    request=async()=>{throw new Error('Offline');};
    assert.equal(await (await load()).text(),'fresh shell');
    request=async()=>new Response('unavailable',{status:503});
    assert.equal(await (await load()).text(),'fresh shell');
    request=async()=>({ok:true,redirected:true});
    assert.equal(await (await load()).text(),'fresh shell');
    request=(url,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('Timeout'))));
    assert.equal(await (await load()).text(),'fresh shell');
    request=async(url,{signal})=>new Response(new ReadableStream({start(controller){
        signal.addEventListener('abort',()=>controller.error(new Error('Body timeout')));
    }}));
    assert.equal(await (await load()).text(),'fresh shell');
    assert.equal(await (await bucket.match('/')).text(),'fresh shell');
    request=async()=>{throw new Error('Offline');};
    assert.equal((await load('https://autoxak.example/setup')).status,0);
    assert.equal(await load('https://autoxak.example/api/v1/auth/me'),undefined);
    assert.equal(await load('https://another.example/'),undefined);
    assert.equal(await load(undefined,'POST'),undefined);
    let activation;
    handlers.activate({waitUntil:promise=>{activation=promise;}});
    await activation;
    assert(buckets.has('autoxak-shell-v3') && buckets.has('unrelated-app') && !buckets.has('autoxak-shell-v2'));
    console.log('Service worker: fresh/cached shell, 503, redirected login, stalled headers/body, query canonicalization, API bypass, cache upgrade passed');
})().catch(error=>{console.error(error);process.exit(1);});
