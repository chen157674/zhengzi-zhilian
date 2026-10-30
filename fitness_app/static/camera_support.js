// 手机浏览器直接采集；不访问设备，函数本身可做尺寸/权限条件检查。
export function cameraConstraints(deviceId='',facing='user'){
  return {audio:false,video:{width:{ideal:1280},height:{ideal:720},frameRate:{ideal:24,max:30},...(deviceId?{deviceId:{exact:deviceId}}:{facingMode:{ideal:facing}})}};
}
export function cameraAvailability(environment=globalThis){
  if(!environment.isSecureContext)return '手机摄像头需要通过HTTPS打开本站；请使用已配置的安全访问地址。';
  if(!environment.navigator?.mediaDevices?.getUserMedia)return '当前浏览器不支持摄像头。请使用系统Safari、Chrome或Edge打开本站。';
  return '';
}
export function captureDimensions(width,height){
  if(!Number.isFinite(width)||!Number.isFinite(height)||width<1||height<1)throw new Error('尚未取得有效相机画面。');
  const scale=Math.min(1,1280/Math.max(width,height),720/Math.min(width,height));
  return {width:Math.max(1,Math.floor(width*scale)),height:Math.max(1,Math.floor(height*scale))};
}
