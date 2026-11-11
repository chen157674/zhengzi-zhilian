import test from 'node:test';
import assert from 'node:assert/strict';
import {angle,Analyzer} from '../web/analyzer.mjs';

function pose(points) {
  const p=Array.from({length:33},()=>({x:.5,y:.5,visibility:0}));
  for(let side=0;side<2;side++)[11,13,15,23,25,27].forEach((id,i)=>p[id+side]={...points[i],visibility:.95});
  return [p];
}
function squat(degrees) {
  const a=degrees*Math.PI/180, hip={x:.5+.2*Math.sin(a),y:.6+.2*Math.cos(a)};
  return pose([{x:hip.x,y:hip.y-.2},{x:hip.x-.05,y:hip.y-.15},{x:hip.x-.1,y:hip.y-.12},hip,{x:.5,y:.6},{x:.5,y:.9}]);
}
const plank=(hipY=.45)=>pose([{x:.2,y:.4},{x:.2,y:.58},{x:.38,y:.58},{x:.55,y:hipY},{x:.7,y:.48},{x:.9,y:.5}]);
const tick=(a,p,t,w=1000,h=1000)=>a.update(p,t,w,h);

test('angles respect image aspect ratio through pixel coordinates',()=>{
  assert.equal(angle({x:0,y:1},{x:0,y:0},{x:1,y:0}),90);
  assert.ok(Math.abs(angle({x:0,y:100},{x:0,y:0},{x:200,y:100})-63.4349488)<.0001);
  assert.equal(angle({x:0,y:0},{x:0,y:0},{x:1,y:0}),null);
});
test('a front view may count but must not claim a depth assessment',()=>{
  const a=new Analyzer('squat');
  for(const [t,n] of [[0,180],[.3,180],[.5,120],[.8,115],[1,120],[1.2,180],[1.5,180]]){
    const p=squat(n);p[0][11].x=.25;p[0][12].x=.75;tick(a,p,t);
  }
  assert.equal(a.reps.length,1);assert.equal(a.reps[0].assessed,false);assert.equal(a.reps[0].issue,null);
});
test('one complete standing-down-standing cycle counts once; a shallow cycle gets its own observation',()=>{
  const a=new Analyzer('squat');
  [[0,180],[.15,180],[.3,180],[.5,120],[.8,115],[1,120],[1.2,180],[1.5,180],[1.8,180]].forEach(([t,n])=>tick(a,squat(n),t));
  assert.equal(a.reps.length,1);assert.match(a.reps[0].issue,/幅度/);
});
test('starting mid-squat or losing tracking does not fabricate a repetition',()=>{
  const a=new Analyzer('squat');
  [[0,90],[.3,90],[.5,180],[.8,180],[1,90]].forEach(([t,n])=>tick(a,squat(n),t));
  assert.equal(a.reps.length,0);
  tick(a,[],1.1);tick(a,squat(180),1.3);tick(a,squat(180),1.6);
  assert.equal(a.reps.length,0);
});
test('pause and frame gaps do not add plank holding time',()=>{
  const a=new Analyzer('plank');
  for(let i=0;i<=10;i++)tick(a,plank(),i*.1);
  assert.ok(Math.abs(a.hold-1)<.001);
  a.breakTracking();tick(a,plank(),10);tick(a,plank(),10.1);
  assert.ok(Math.abs(a.hold-1.1)<.001);
  tick(a,plank(),20);assert.ok(Math.abs(a.hold-1.1)<.001);
  tick(a,[],20.1);tick(a,plank(),30);assert.ok(Math.abs(a.hold-1.1)<.001);
});
test('standing is not a plank, multiple people and off-frame joints are rejected',()=>{
  const a=new Analyzer('plank');assert.equal(tick(a,squat(180),0).valid,false);
  assert.equal(tick(a,[...plank(),...plank()],.1).valid,false);
  const p=plank();p[0][11].x=1;p[0][12].x=1;
  assert.equal(tick(a,p,.2).valid,false);assert.equal(a.hold,0);
});
test('plank hints debounce, record intervals, and close on tracking loss',()=>{
  const a=new Analyzer('plank');
  for(let i=0;i<=20;i++)tick(a,plank(.28),i*.1);
  assert.match(a.snapshot().intervals[0].issue,/偏高/);
  const hold=a.hold;tick(a,[],2.1);
  assert.equal(a.hold,hold);assert.equal(a.snapshot().intervals.length,1);
  assert.equal(a.snapshot().intervals[0].end,2);
});
