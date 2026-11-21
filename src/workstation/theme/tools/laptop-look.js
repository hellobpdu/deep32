// Exact resources and measured superseded-path removals; no drawing code.
'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto');
const sha=b=>crypto.createHash('sha256').update(b).digest('hex');
// Restore rows the generator's own output replaces (alpha.2: the dthello text names the code's license and the
// NOTICE). Their stored bytes are still checked; they are not written.
const SUPERSEDED=new Set(['etc/dt/config/C/d32.copyright']);
exports.overlay=function(root){
  const base=path.resolve(__dirname,'../laptop-look');
  const data=fs.readFileSync(path.join(base,'manifest.json'));
  const removedData=fs.readFileSync(path.join(base,'removed-paths.json'));
  if(sha(data)!==/* SHA-256 */'4a717f650e9e34a1a5cee8461c72e69232503970bddca90180107b002cf3bb2c'||sha(removedData)!==/* SHA-256 */'39f308d9c949d404d06ba3b620fdd9b1ecd4c87dd3c066ba67646efdf7e2176f')throw new Error('laptop-look metadata differs');
  const rows=JSON.parse(data), removed=JSON.parse(removedData), seen=new Set();
  function plain(rel){if(!rel||rel.startsWith('/')||rel.includes(String.fromCharCode(92))||rel.includes(':')||rel.split('/').some(p=>!p||p==='.'||p==='..'))throw new Error('unsafe laptop path');}
  function stat(p){try{return fs.lstatSync(p);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
  function destination(rel){plain(rel);const dir=path.resolve(root.dir),dest=path.resolve(dir,rel);for(let p=dest;p!==dir;p=path.dirname(p)){const s=stat(p);if(s&&(s.isSymbolicLink()||(p===dest?!s.isFile():!s.isDirectory())))throw new Error('nonplain laptop target/parent: '+rel);}return dest;}
  const validated=rows.map(r=>{destination(r.path);if(seen.has(r.path)||!/^[a-f0-9]{64}$/.test(r.sha256))throw new Error('duplicate or invalid laptop row');seen.add(r.path);const b=fs.readFileSync(path.join(base,'store',r.sha256));if(b.length!==r.bytes||sha(b)!==r.sha256)throw new Error('laptop resource differs: '+r.path);return[r,b];});
  for(const s of SUPERSEDED)if(!seen.has(s))throw new Error('superseded laptop row absent: '+s);
  const removeSeen=new Set();
  const removePaths=removed.map(r=>{if(!r.installed_path.startsWith('/'))throw new Error('removal is not absolute');const rel=r.installed_path.slice(1),dest=destination(rel);if(seen.has(rel)||removeSeen.has(rel))throw new Error('restore/removal collision');removeSeen.add(rel);if(stat(dest)&&sha(fs.readFileSync(dest))!==r.before_sha256)throw new Error('generated removal drift: '+rel);return[rel,dest];});
  for(const[r,b]of validated){if(SUPERSEDED.has(r.path))continue;root.files=root.files.filter(f=>f.rel!==r.path);root.write(r.path,b,{mode:r.mode,owner:'root:wheel',install:'file',licence:r.licence,note:'laptop byte restore / measured permitted rule'});}
  for(const[rel,dest]of removePaths){if(fs.existsSync(dest))fs.unlinkSync(dest);root.files=root.files.filter(f=>f.rel!==rel);}
};
