import test from 'node:test';
import assert from 'node:assert/strict';
import {coachingCue,recordCue,videoCue} from '../fitness_app/static/correction_cue.js';
import {feedbackSummary} from '../fitness_app/static/feedback_summary.js';

test('缺少结果、观察动作、取景失败不能显示正常',()=>{
  for(const value of [null,{}, {state:'moving'},{state:'evidence_insufficient'},{state:'positioning'}])assert.notEqual(coachingCue(value).state,'clear');
  assert.equal(coachingCue({state:'observing'}).title,'正在观察');
  assert.deepEqual(coachingCue({state:'needs_adjustment',primary_cue:{message:'右肘外移',suggestion:'收回右肘'}}),{state:'issue',title:'右肘外移',instruction:'收回右肘'});
});
test('机位限制不冒充看不清，记录重复问题合并，无评估不会显示正常',()=>{
  assert.equal(coachingCue({state:'evidence_insufficient'}).title,'分析中');
  assert.notEqual(feedbackSummary({posture:{findings:[{status:'unassessable'}]}}).state,'clear');
  const rows=[{code:'x',status:'issue',start_ms:0,end_ms:1000},{code:'x',status:'issue',start_ms:1000,end_ms:2000},{code:'x',status:'issue',start_ms:4000,end_ms:5000}];
  assert.equal(feedbackSummary({posture:{findings:rows}}).issues.length,2);
  assert.equal(rows[0].end_ms,1000);
  assert.equal(feedbackSummary({posture:{findings:[{status:'not_observed'}]}}).title,'已检查项目未见偏差');
});
test('回看严格匹配原视频时间，问题优先，未知范围不继承正常',()=>{
  const record={posture:{findings:[{status:'not_observed',start_ms:100,end_ms:300},{status:'issue',start_ms:150,end_ms:200,message:'手臂不齐',suggestion:'保持双手同高'}]}};
  assert.equal(recordCue(record,99).state,'unassessable');
  assert.equal(recordCue(record,100).state,'clear');
  assert.equal(recordCue(record,150).state,'issue');
  assert.equal(recordCue(record,200).state,'clear');
  assert.equal(recordCue(record,300).state,'unassessable');
  assert.equal(recordCue(null,150).state,'unassessable');
});


test('视频分析全程显示正在观察，不提前展示问题或正常',()=>{
  const record={posture:{findings:[{status:'issue',start_ms:0,end_ms:20000,message:'身体向右侧持续倾斜',suggestion:'保持肩髋对齐'}]}};
  for(const state of ['observing','moving','evidence_insufficient','needs_adjustment']){
    const job={stopped:false,status:'running',save_state:'clean',coaching:{state,primary_cue:{message:'身体向右侧持续倾斜'}}};
    assert.deepEqual(videoCue(job,record,15000),{state:'observing',title:'正在观察',instruction:''});
  }
});
test('完成后回看真实问题和对应建议，不向区间外延伸',()=>{
  const job={stopped:true,status:'completed',save_state:'clean'},record={posture:{findings:[{status:'issue',start_ms:15000,end_ms:16000,message:'身体向右侧持续倾斜',suggestion:'保持肩髋对齐'}]}};
  const before=structuredClone(record);
  assert.equal(videoCue(job,record,15500).title,'身体向右侧持续倾斜');
  assert.equal(videoCue(job,record,15500).instruction,'保持肩髋对齐');
  for(const stamp of [10000,16000,20000])assert.notEqual(videoCue(job,record,stamp).state,'issue');
  assert.deepEqual(record,before);
});
test('保存失败和中断优先保留，不被正在观察遮盖',()=>{
  assert.equal(videoCue({stopped:false,save_state:'save_failed'},null,15000).title,'结果尚未保存');
  assert.equal(videoCue({stopped:false,save_state:'clean',coaching:{state:'interrupted'}},null,15000).title,'分析中断');
  assert.equal(videoCue(null,null,0).title,'等待分析');
});
