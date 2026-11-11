import test from 'node:test';
import assert from 'node:assert/strict';
import {predictionTiming} from '../fitness_app/static/experiment_format.js';

test('低于原时钟分辨率的记录不能宣称零耗时或支持细微速度比较',()=>{
  assert.equal(predictionTiming([{prediction_ms:0}],{resolution_ms:15.625}).text,'计时精度不足');
  assert.equal(predictionTiming([{prediction_ms:0},{prediction_ms:15.625}],{resolution_ms:15.625}).text,'计时精度不足');
  assert.equal(predictionTiming([{prediction_ms:20}],{resolution_ms:15.625}).text,'20.000 ms');
  assert.equal(predictionTiming([],null).text,'不可计算');
  assert.equal(predictionTiming([{prediction_ms:1}],null).text,'计时信息不足');
});
