const assert=require('assert');
const fs=require('fs');
const dashboard=JSON.parse(fs.readFileSync(process.argv[2],'utf8'));
const view=dashboard.views.find(v=>v.path==='forecast');
const charts=view.sections.flatMap(s=>s.cards).filter(c=>c.type==='custom:apexcharts-card');
assert.equal(charts.length,6);
assert.equal(charts.reduce((n,c)=>n+c.series.length,0),8);
const now=Date.parse('2026-01-15T12:07:00Z');
Date.now=()=>now;
const q0=Math.floor(now/900000)*900;
const rows=Array.from({length:25},(_,i)=>({start:q0+i*900,
 house_heat_demand_kw:3,heat_demand_kw:2,ventilation_contribution_kw:1,
 pv_kw:.5,outdoor:-2,import_price:-.1,heat_cost:-.03,cop:4}));
let count=0;
for(const c of charts)for(const s of c.series){
 const fn=new Function('entity','start','end',s.data_generator);
 const run=f=>fn({attributes:{result:f}},new Date(now),new Date(now+21600000));
 const f={complete:true,valid_until:now/1000+1200,quarters:rows};
 const points=run(f);
 assert.equal(points[0][0],now);
 assert.equal(points.at(-1)[0],now+21600000);
 assert(points.every(p=>Number.isFinite(p[0])&&Number.isFinite(p[1])));
 assert.deepEqual(run({...f,complete:false}),[]);
 assert.deepEqual(run({...f,valid_until:now/1000}),[]);
 assert.deepEqual(run({...f,valid_until:undefined}),[]);
 assert.deepEqual(run({...f,quarters:[]}),[]);
 if(s.unit.startsWith('EUR/')||s.unit==='°C')assert(points[0][1]<0);
 assert.equal(s.float_precision,s.unit.startsWith('EUR/')?2:1);
 count++;
}
console.log(`Forecast: ${charts.length} graphs / ${count} series; valid coverage, expiry, missing and negative values passed.`);
