// 独立线程运行同步推理，避免视频分析阻塞按钮和页面。
importScripts('./vendor/vision_bundle.js');
let detector;
onmessage = async ({data}) => {
  if (data.type === 'init') {
    try {
      const files = await Vision.FilesetResolver.forVisionTasks(new URL('./vendor/wasm',self.location).href);
      detector = await Vision.PoseLandmarker.createFromOptions(files, {
        baseOptions: {modelAssetPath:new URL('./vendor/pose_landmarker_lite.task',self.location).href, delegate:'CPU'},
        runningMode:'VIDEO',numPoses:2,minPoseDetectionConfidence:.6,minPosePresenceConfidence:.6,minTrackingConfidence:.6,
      });
      postMessage({type:'ready'});
    } catch (error) { postMessage({type:'error',message:String(error.message ?? error)}); }
  }
  if (data.type === 'frame') {
    try {
      const result = detector.detectForVideo(data.frame, data.timestamp);
      postMessage({type:'result', poses:result.landmarks, epoch:data.epoch, time:data.time, width:data.width,height:data.height});
    } catch (error) { postMessage({type:'error',message:String(error.message ?? error)}); }
    finally { data.frame.close(); }
  }
};
