// 基础规则开发预览。阈值是工程起点，尚无真实测试集的准确率结论。
export const ACTIONS = {
  squat: {name: '徒手深蹲', unit: '次', setup: '侧面拍摄，头、肩、髋、膝和脚踝完整入镜。先站直，再开始动作。',
    scope: '当前观察动作周期与下蹲幅度，不判断膝内扣、腰背细节或肌肉发力。'},
  plank: {name: '前臂平板支撑', unit: '秒', setup: '侧面拍摄，前臂支撑地面，让肩、肘、手腕、髋、膝和脚踝清楚可见。',
    scope: '当前观察保持时长与髋部相对位置，不判断脊柱曲度或肌肉发力。'},
};

export function angle(a, b, c) {
  const u = [a.x-b.x, a.y-b.y], v = [c.x-b.x, c.y-b.y];
  const norm = Math.hypot(...u)*Math.hypot(...v);
  return norm < 1e-6 ? null : Math.acos(Math.max(-1, Math.min(1, (u[0]*v[0]+u[1]*v[1])/norm)))*180/Math.PI;
}

export class Analyzer {
  constructor(action) { this.action = action; this.reps = []; this.intervals = []; this.hold = 0; this.observed = 0;
    this.lastTime = null; this.previousValid = false; this.phase = 'ready'; this.lowAt = null;
    this.minAngle = 180; this.pending = null; this.pendingAt = null; this.issue = null; this.issueAt = null;
    this.standingSince = null; this.armed = false; this.lastSide = null; this.repAssessable = true; }

  closeIssue(time) {
    if (this.issue && this.issueAt !== null && time > this.issueAt)
      this.intervals.push({start: this.issueAt, end: time, issue: this.issue});
    this.issue = null; this.issueAt = null;
  }

  breakTracking(time = this.lastTime) {
    this.closeIssue(time ?? 0);
    this.phase = 'ready'; this.lowAt = null; this.armed = false; this.standingSince = null;
    this.minAngle = 180; this.previousValid = false; this.lastTime = null; this.repAssessable = true;
    this.pending = null; this.pendingAt = null; this.lastSide = null;
  }

  snapshot(time = this.lastTime) {
    const intervals = this.intervals.map(x => ({...x}));
    if (this.issue && time > this.issueAt) intervals.push({start: this.issueAt, end: time, issue: this.issue});
    return {reps: this.reps, intervals, hold: this.hold, observed: this.observed};
  }

  update(poses, time, width, height) {
    const fail = message => { this.breakTracking(this.lastTime); return {valid: false, message, phase: '等待入镜'}; };
    if (poses.length !== 1) return fail(poses.length > 1 ? '画面中有多人，请保持单人入镜。' : '未看到完整人体，请调整距离和光线。');
    if (!Number.isFinite(time) || width <= 0 || height <= 0) return fail('画面时间或尺寸无效。');
    const p = poses[0];
    const sides = [[11,13,15,23,25,27], [12,14,16,24,26,28]];
    const visible = i => p[i] && p[i].visibility >= .65 && p[i].x > .015 && p[i].x < .985 && p[i].y > .015 && p[i].y < .985;
    const ranked = sides.map(ids => ({ids, score: Math.min(...ids.map(i => p[i]?.visibility ?? 0))}))
      .filter(({ids}) => ids.every(visible)).sort((a,b) => b.score-a.score);
    if (!ranked.length) return fail('关键部位被遮挡或出框，请完整拍到肩、手臂、髋、膝和脚踝。');
    // 固定跟踪一侧，避免可见性变化使左右角度混在同一个动作周期内。
    let side = ranked.find(s => s.ids[0] === this.lastSide) ?? ranked[0];
    if (this.lastSide !== null && this.lastSide !== side.ids[0]) this.breakTracking(time);
    this.lastSide = side.ids[0];
    const [shoulder,elbow,wrist,hip,knee,ankle] = side.ids.map(i => ({x:p[i].x*width,y:p[i].y*height}));
    const body = Math.hypot(shoulder.x-ankle.x,shoulder.y-ankle.y);
    if (body < height*.18) return fail('人体在画面中过小，请靠近一些。');
    const gap = this.lastTime === null ? 0 : time-this.lastTime;
    if (gap < 0 || gap > .65) {
      this.breakTracking(this.lastTime); this.lastSide = side.ids[0];
    }
    const dt = this.previousValid && gap > 0 && gap <= .65 ? gap : 0;
    this.lastTime = time;
    if (this.action === 'squat') {
      if (shoulder.y >= hip.y || hip.y >= ankle.y || Math.abs(shoulder.y-ankle.y) < Math.abs(shoulder.x-ankle.x))
        return fail('请站立并从侧面拍摄，当前姿态不适合深蹲计数。');
      this.observed += dt; this.previousValid = true;
      const bend = angle(hip,knee,ankle);
      if (bend === null) return fail('腿部关键点不稳定。');
      const shoulderSpan = Math.abs(p[11].x-p[12].x)*width;
      const torso = Math.hypot(shoulder.x-hip.x,shoulder.y-hip.y);
      const sideView = shoulderSpan < torso*.5;
      if (bend >= 155) {
        if (this.standingSince === null) this.standingSince = time;
        if (time-this.standingSince >= .25) {
          if (this.phase === 'down' && this.lowAt !== null && time-this.lowAt >= .45 && time-this.lowAt <= 20) {
            const assessed=this.repAssessable && sideView;
            this.reps.push({index:this.reps.length+1,start:this.lowAt,end:time,assessed,
              issue:assessed && this.minAngle > 105 ? '幅度偏小（参考规则）' : null, minAngle:Math.round(this.minAngle)});
          }
          this.phase = 'ready'; this.lowAt = null; this.minAngle = 180; this.armed = true; this.repAssessable = true;
        }
      } else {
        this.standingSince = null;
        if (this.armed && bend < 130 && this.phase !== 'down') {
          this.phase = 'down'; this.lowAt = time;
        }
        if (this.phase === 'down') {
          this.repAssessable = this.repAssessable && sideView;
          this.minAngle = Math.min(this.minAngle,bend);
          if (time-this.lowAt > 20) { this.phase = 'ready'; this.lowAt = null; this.armed = false; }
        }
      }
      return {valid:true, phase:!this.armed ? '先站直' : this.phase === 'down' ? '下蹲中' : '准备下一次',
        message:!this.armed ? '先稳定站直，让系统确认起始姿态。' : !sideView ? '当前机位仅尝试计数；幅度评估请改为侧面拍摄。' : '保持侧面入镜，完成站立、下蹲、再站起的完整周期。', angle:bend};
    }
    const elbowAngle = angle(shoulder,elbow,wrist);
    const horizontal = Math.abs(shoulder.x-ankle.x) > Math.abs(shoulder.y-ankle.y)*1.8;
    const supported = horizontal && shoulder.y < elbow.y && Math.abs(wrist.y-elbow.y) < body*.22 && elbowAngle > 45 && elbowAngle < 140;
    if (!supported) return fail('请采用前臂支撑姿势，侧面完整入镜后才累计保持时间。');
    const ratio = (hip.x-shoulder.x)/(ankle.x-shoulder.x);
    if (ratio < .1 || ratio > .9) return fail('身体方向不清楚，请调整侧面机位。');
    this.hold += dt; this.observed += dt; this.previousValid = true;
    const lineY = shoulder.y + ratio*(ankle.y-shoulder.y);
    const offset = (hip.y-lineY)/body;
    const candidate = offset < -.075 ? '髋部偏高（参考规则）' : offset > .075 ? '髋部偏低（参考规则）' : null;
    if (this.pending !== candidate) { this.pending = candidate; this.pendingAt = time; }
    if (candidate !== this.issue && time-this.pendingAt >= .9) {
      this.closeIssue(time); this.issue = candidate; this.issueAt = candidate ? time : null;
    }
    return {valid:true, phase:'保持中', message:this.issue ?? '正在记录保持时间；未触发当前支持的姿态提示。', issue:this.issue};
  }
}
