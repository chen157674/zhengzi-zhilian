"""SiTrEx角度前处理：保留上游数值定义，去除TensorFlow数据生成器依赖。
Copyright (c) 2025 Abdellah SELLAM. MIT，许可见licenses/sitrex.txt。
来源固定提交1eb3d7d431a6b52ba07afca0cde55d9f33822d43。
"""
import numpy as np

def normalize(v):
    norm = np.linalg.norm(v)
    return v / norm if norm != 0 else v

def point(landmark):
    return np.array([landmark['x'], landmark['y'], landmark['z']])

def angle_between(a, b):
    cosine_angle = np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-6)
    angle = np.arccos(np.clip(cosine_angle, -1.0, 1.0))
    return np.degrees(angle)

def angle_between_signed(v1, v2, eps=1e-8):
    # normalize
    v1_norm = np.linalg.norm(v1) + eps
    v2_norm = np.linalg.norm(v2) + eps
    v1u = v1 / v1_norm
    v2u = v2 / v2_norm
    parent_unit = v1u  # use parent bone as local ref

    cross_prod = np.cross(v1u, v2u)
    num = np.dot(parent_unit, cross_prod)        # signed numerator
    den = np.dot(v1u, v2u)                       # denominator (dot)
    angle = np.degrees(np.arctan2(num, den))
    if angle < 0:
        return angle + 360
    return angle

def vector(start, end):
    return np.array([end['x'] - start['x'], end['y'] - start['y'], end['z'] - start['z']])

def calculate_angle(a, b, c):
    return angle_between(vector(b, a), vector(b, c))

def calculate_angle_signed(a, b, c):
    return angle_between_signed(vector(b, a), vector(b, c))

def midpoint(a, b):
    return (a + b) / 2

def vector_plane_project(vector, plane_normal):
    vector_proj = vector - np.dot(vector, plane_normal) * plane_normal
    vector_proj = normalize(vector_proj)
    return vector_proj

def signed_angle(v1, v2, n, eps=1e-7):
    v1p = v1 - np.dot(v1, n) * n
    v2p = v2 - np.dot(v2, n) * n
    v1u = v1p / (np.linalg.norm(v1p) + eps)
    v2u = v2p / (np.linalg.norm(v2p) + eps)
    cross = np.cross(v1u, v2u)
    num = np.dot(cross, n) # sin or -sin
    den = np.dot(v1u, v2u) # cos
    angle = np.degrees(np.arctan2(num, den))
    if angle < 0:
        return angle + 360
    return angle

def calculate_angles(landmarks, angle_type):
    # Define landmark indices
    LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
    LEFT_ELBOW, RIGHT_ELBOW = 13, 14
    LEFT_WRIST, RIGHT_WRIST = 15, 16
    LEFT_WRIST, RIGHT_WRIST = 15, 16
    LEFT_PINKY, RIGHT_PINKY = 17, 18
    LEFT_INDEX,RIGHT_INDEX = 19, 20
    LEFT_HIP, RIGHT_HIP = 23, 24
    LEFT_KNEE, RIGHT_KNEE = 25, 26
    LEFT_ANKLE, RIGHT_ANKLE = 27, 28

    # Estimating Primary Vectors
    L_shoulder = point(landmarks[LEFT_SHOULDER])  # LEFT_SHOULDER
    R_shoulder = point(landmarks[RIGHT_SHOULDER])  # RIGHT_SHOULDER
    L_hip = point(landmarks[LEFT_HIP])       # LEFT_HIP
    R_hip = point(landmarks[RIGHT_HIP])       # RIGHT_HIP
    L_ankle = point(landmarks[LEFT_ANKLE])       # LEFT_ANKLE
    R_ankle = point(landmarks[RIGHT_ANKLE])       # RIGHT_ANKLE

    # Midpoints
    mid_shoulder = midpoint(L_shoulder, R_shoulder)
    mid_hip = midpoint(L_hip, R_hip)

    # Local torso axes
    horizontal = normalize(R_shoulder - L_shoulder)           # Local X (shoulder width)
    upward = normalize(mid_shoulder - mid_hip)          # Local Y (torso up)
    forward = normalize(np.cross(horizontal, upward))          # Local Z (front-back normal to torso plane)
    # Re-orthogonalize Y just in case (to maintain right-handed frame)
    upward = normalize(np.cross(forward, horizontal))

    # Torso flexion
    mid_ankle = midpoint(L_ankle, R_ankle)
    torso = mid_shoulder - mid_hip
    gravity = mid_ankle - mid_hip

    if angle_type == 'unsigned':
        torso_flexion = angle_between(torso, gravity)
    
        # Shoulder
        left_shoulder = calculate_angle(landmarks[LEFT_HIP], landmarks[LEFT_SHOULDER], landmarks[LEFT_ELBOW])
        right_shoulder = calculate_angle(landmarks[RIGHT_HIP], landmarks[RIGHT_SHOULDER], landmarks[RIGHT_ELBOW])
    
        # Elbow
        left_elbow = calculate_angle(landmarks[LEFT_SHOULDER], landmarks[LEFT_ELBOW], landmarks[LEFT_WRIST])
        right_elbow = calculate_angle(landmarks[RIGHT_SHOULDER], landmarks[RIGHT_ELBOW], landmarks[RIGHT_WRIST])

        # Wrist
        left_hand = midpoint(point(landmarks[LEFT_PINKY]), point(landmarks[LEFT_INDEX]))
        left_wrist = angle_between(left_hand - point(landmarks[LEFT_WRIST]),
                                   point(landmarks[LEFT_ELBOW]) - point(landmarks[LEFT_WRIST]))
        right_hand = midpoint(point(landmarks[RIGHT_PINKY]), point(landmarks[RIGHT_INDEX]))
        right_wrist = angle_between(right_hand - point(landmarks[RIGHT_WRIST]),
                                    point(landmarks[RIGHT_ELBOW]) - point(landmarks[RIGHT_WRIST]))
        
        # Knee
        left_knee = calculate_angle(landmarks[LEFT_HIP], landmarks[LEFT_KNEE], landmarks[LEFT_ANKLE])
        right_knee = calculate_angle(landmarks[RIGHT_HIP], landmarks[RIGHT_KNEE], landmarks[RIGHT_ANKLE])
    
        # Hip
        left_hip = calculate_angle(landmarks[LEFT_SHOULDER], landmarks[LEFT_HIP], landmarks[LEFT_KNEE])
        right_hip = calculate_angle(landmarks[RIGHT_SHOULDER], landmarks[RIGHT_HIP], landmarks[RIGHT_KNEE])

        # Scapular upward rotation
        left_scapular_upward_rotation = angle_between(vector(landmarks[LEFT_HIP], landmarks[LEFT_SHOULDER]),
                                                      vector(landmarks[LEFT_HIP], landmarks[RIGHT_HIP]))
    
        right_scapular_upward_rotation = angle_between(vector(landmarks[RIGHT_HIP], landmarks[RIGHT_SHOULDER]),
                                                       vector(landmarks[RIGHT_HIP], landmarks[LEFT_HIP]))
    
        # Shoulder abduction/extension
        v_arm_left = vector(landmarks[LEFT_SHOULDER],  landmarks[LEFT_ELBOW])
        f_proj_v_arm_left = vector_plane_project(v_arm_left, forward)
        f_left_shoulder_abduction = angle_between(f_proj_v_arm_left, upward)
        h_proj_v_arm_left = vector_plane_project(v_arm_left, upward)
        h_left_shoulder_adduction = angle_between(h_proj_v_arm_left, forward)
        s_proj_v_arm_left = vector_plane_project(v_arm_left, horizontal)
        left_shoulder_extension = angle_between(s_proj_v_arm_left, upward)
    
        v_arm_right = vector(landmarks[RIGHT_SHOULDER],  landmarks[RIGHT_ELBOW])
        f_proj_v_arm_right = vector_plane_project(v_arm_right, forward)
        f_right_shoulder_abduction = angle_between(f_proj_v_arm_right, upward)
        h_proj_v_arm_right = vector_plane_project(v_arm_right, upward)
        h_right_shoulder_adduction = angle_between(h_proj_v_arm_right, forward)
        s_proj_v_arm_right = vector_plane_project(v_arm_right, horizontal)
        right_shoulder_extension = angle_between(s_proj_v_arm_right, upward)
    
        # Ankle dorsiflexion
        v_shin_left = vector(landmarks[LEFT_KNEE],  landmarks[LEFT_ANKLE])
        s_proj_v_shin_left = vector_plane_project(v_shin_left, horizontal)
        left_ankle_dorsiflexion = angle_between(s_proj_v_shin_left, upward)
    
        v_shin_right = vector(landmarks[RIGHT_KNEE],  landmarks[RIGHT_ANKLE])
        s_proj_v_shin_right = vector_plane_project(v_shin_right, horizontal)
        right_ankle_dorsiflexion = angle_between(s_proj_v_shin_right, upward)
    
        # Left knee valgus indicator
        f_proj_left_hip = vector_plane_project(point(landmarks[LEFT_HIP]), forward)
        f_proj_left_knee = vector_plane_project(point(landmarks[LEFT_KNEE]), forward)
        f_proj_left_ankle = vector_plane_project(point(landmarks[LEFT_ANKLE]), forward)
        left_knee_valgus_indicator = angle_between(f_proj_left_hip - f_proj_left_knee, f_proj_left_ankle - f_proj_left_knee)
        # Right knee valgus indicator
        f_proj_right_hip = vector_plane_project(point(landmarks[RIGHT_HIP]), forward)
        f_proj_right_knee = vector_plane_project(point(landmarks[RIGHT_KNEE]), forward)
        f_proj_right_ankle = vector_plane_project(point(landmarks[RIGHT_ANKLE]), forward)
        right_knee_valgus_indicator = angle_between(f_proj_right_hip - f_proj_right_knee, f_proj_right_ankle - f_proj_right_knee)
        
    elif angle_type == 'signed':
        torso_flexion = angle_between(torso, gravity)
    
        # Shoulder
        left_shoulder = calculate_angle_signed(landmarks[LEFT_HIP], landmarks[LEFT_SHOULDER], landmarks[LEFT_ELBOW])
        right_shoulder = calculate_angle_signed(landmarks[RIGHT_HIP], landmarks[RIGHT_SHOULDER], landmarks[RIGHT_ELBOW])
    
        # Elbow
        left_elbow = calculate_angle_signed(landmarks[LEFT_SHOULDER], landmarks[LEFT_ELBOW], landmarks[LEFT_WRIST])
        right_elbow = calculate_angle_signed(landmarks[RIGHT_SHOULDER], landmarks[RIGHT_ELBOW], landmarks[RIGHT_WRIST])

        # Wrist
        left_hand = midpoint(point(landmarks[LEFT_PINKY]), point(landmarks[LEFT_INDEX]))
        left_wrist = angle_between_signed(left_hand - point(landmarks[LEFT_WRIST]),
                                   point(landmarks[LEFT_ELBOW]) - point(landmarks[LEFT_WRIST]))
        right_hand = midpoint(point(landmarks[RIGHT_PINKY]), point(landmarks[RIGHT_INDEX]))
        right_wrist = angle_between_signed(right_hand - point(landmarks[RIGHT_WRIST]),
                                    point(landmarks[RIGHT_ELBOW]) - point(landmarks[RIGHT_WRIST]))
        
        # Knee
        left_knee = calculate_angle_signed(landmarks[LEFT_HIP], landmarks[LEFT_KNEE], landmarks[LEFT_ANKLE])
        right_knee = calculate_angle_signed(landmarks[RIGHT_HIP], landmarks[RIGHT_KNEE], landmarks[RIGHT_ANKLE])
    
        # Hip
        left_hip = calculate_angle_signed(landmarks[LEFT_SHOULDER], landmarks[LEFT_HIP], landmarks[LEFT_KNEE])
        right_hip = calculate_angle_signed(landmarks[RIGHT_SHOULDER], landmarks[RIGHT_HIP], landmarks[RIGHT_KNEE])

        # Scapular upward rotation
        left_scapular_upward_rotation = angle_between_signed(vector(landmarks[LEFT_HIP], landmarks[LEFT_SHOULDER]),
                                                      vector(landmarks[LEFT_HIP], landmarks[RIGHT_HIP]))
    
        right_scapular_upward_rotation = angle_between_signed(vector(landmarks[RIGHT_HIP], landmarks[RIGHT_SHOULDER]),
                                                       vector(landmarks[RIGHT_HIP], landmarks[LEFT_HIP]))
    
        # Shoulder abduction/extension
        v_arm_left = vector(landmarks[LEFT_SHOULDER],  landmarks[LEFT_ELBOW])
        f_proj_v_arm_left = vector_plane_project(v_arm_left, forward)
        f_left_shoulder_abduction = angle_between_signed(f_proj_v_arm_left, upward)
        h_proj_v_arm_left = vector_plane_project(v_arm_left, upward)
        h_left_shoulder_adduction = angle_between_signed(h_proj_v_arm_left, forward)
        s_proj_v_arm_left = vector_plane_project(v_arm_left, horizontal)
        left_shoulder_extension = angle_between_signed(s_proj_v_arm_left, upward)
    
        v_arm_right = vector(landmarks[RIGHT_SHOULDER],  landmarks[RIGHT_ELBOW])
        f_proj_v_arm_right = vector_plane_project(v_arm_right, forward)
        f_right_shoulder_abduction = angle_between_signed(f_proj_v_arm_right, upward)
        h_proj_v_arm_right = vector_plane_project(v_arm_right, upward)
        h_right_shoulder_adduction = angle_between_signed(h_proj_v_arm_right, forward)
        s_proj_v_arm_right = vector_plane_project(v_arm_right, horizontal)
        right_shoulder_extension = angle_between_signed(s_proj_v_arm_right, upward)
    
        # Ankle dorsiflexion
        v_shin_left = vector(landmarks[LEFT_KNEE],  landmarks[LEFT_ANKLE])
        s_proj_v_shin_left = vector_plane_project(v_shin_left, horizontal)
        left_ankle_dorsiflexion = angle_between_signed(s_proj_v_shin_left, upward)
    
        v_shin_right = vector(landmarks[RIGHT_KNEE],  landmarks[RIGHT_ANKLE])
        s_proj_v_shin_right = vector_plane_project(v_shin_right, horizontal)
        right_ankle_dorsiflexion = angle_between_signed(s_proj_v_shin_right, upward)
    
        # Left knee valgus indicator
        f_proj_left_hip = vector_plane_project(point(landmarks[LEFT_HIP]), forward)
        f_proj_left_knee = vector_plane_project(point(landmarks[LEFT_KNEE]), forward)
        f_proj_left_ankle = vector_plane_project(point(landmarks[LEFT_ANKLE]), forward)
        left_knee_valgus_indicator = angle_between_signed(f_proj_left_hip - f_proj_left_knee, f_proj_left_ankle - f_proj_left_knee)
        # Right knee valgus indicator
        f_proj_right_hip = vector_plane_project(point(landmarks[RIGHT_HIP]), forward)
        f_proj_right_knee = vector_plane_project(point(landmarks[RIGHT_KNEE]), forward)
        f_proj_right_ankle = vector_plane_project(point(landmarks[RIGHT_ANKLE]), forward)
        right_knee_valgus_indicator = angle_between_signed(f_proj_right_hip - f_proj_right_knee, f_proj_right_ankle - f_proj_right_knee)
        
    else:
        normals = [horizontal, forward, upward]
        
        torso_flexion = signed_angle(torso, gravity, horizontal)
    
        # Shoulder
        left_shoulder = [signed_angle(vector(landmarks[LEFT_SHOULDER], landmarks[LEFT_HIP]), 
                                      vector(landmarks[LEFT_SHOULDER], landmarks[LEFT_ELBOW]), 
                                      normal) for normal in normals]
        right_shoulder = [signed_angle(vector(landmarks[RIGHT_SHOULDER], landmarks[RIGHT_HIP]), 
                                       vector(landmarks[RIGHT_SHOULDER], landmarks[RIGHT_ELBOW]),
                                       normal) for normal in normals]
        
        # Elbow
        left_elbow = [signed_angle(vector(landmarks[LEFT_ELBOW], landmarks[LEFT_SHOULDER]), 
                                   vector(landmarks[LEFT_ELBOW], landmarks[LEFT_WRIST]),
                                   normal) for normal in normals]
        right_elbow = [signed_angle(vector(landmarks[RIGHT_ELBOW], landmarks[RIGHT_SHOULDER]), 
                                    vector(landmarks[RIGHT_ELBOW], landmarks[RIGHT_WRIST]),
                                    normal) for normal in normals]
        # Wrist
        left_hand = midpoint(point(landmarks[LEFT_PINKY]), point(landmarks[LEFT_INDEX]))
        left_wrist = [signed_angle(left_hand - point(landmarks[LEFT_WRIST]),
                                   point(landmarks[LEFT_ELBOW]) - point(landmarks[LEFT_WRIST]),
                                   normal) for normal in normals]
        right_hand = midpoint(point(landmarks[RIGHT_PINKY]), point(landmarks[RIGHT_INDEX]))
        right_wrist = [signed_angle(right_hand - point(landmarks[RIGHT_WRIST]),
                                    point(landmarks[RIGHT_ELBOW]) - point(landmarks[RIGHT_WRIST]),
                                    normal) for normal in normals]
        # Knee
        left_knee = [signed_angle(vector(landmarks[LEFT_KNEE], landmarks[LEFT_ANKLE]), 
                                  vector(landmarks[LEFT_KNEE], landmarks[LEFT_HIP]),
                                  normal) for normal in normals]
        right_knee = [signed_angle(vector(landmarks[RIGHT_KNEE], landmarks[RIGHT_ANKLE]), 
                                   vector(landmarks[RIGHT_KNEE], landmarks[RIGHT_HIP]),
                                   normal) for normal in normals]
        # Hip
        left_hip = [signed_angle(vector(landmarks[LEFT_HIP], landmarks[LEFT_SHOULDER]), 
                                 vector(landmarks[LEFT_HIP], landmarks[LEFT_KNEE]),
                                 normal) for normal in normals]
        right_hip = [signed_angle(vector(landmarks[RIGHT_HIP], landmarks[RIGHT_SHOULDER]), 
                                  vector(landmarks[RIGHT_HIP], landmarks[RIGHT_KNEE]),
                                  normal) for normal in normals]
        # Scapular upward rotation
        left_scapular_upward_rotation = signed_angle(vector(landmarks[LEFT_HIP], landmarks[LEFT_SHOULDER]),
                                                     vector(landmarks[LEFT_HIP], landmarks[RIGHT_HIP]),
                                                     forward)
    
        right_scapular_upward_rotation = signed_angle(vector(landmarks[RIGHT_HIP], landmarks[RIGHT_SHOULDER]),
                                                      vector(landmarks[RIGHT_HIP], landmarks[LEFT_HIP]),
                                                      forward)
    
        # Shoulder abduction/extension
        v_arm_left = vector(landmarks[LEFT_SHOULDER],  landmarks[LEFT_ELBOW])
        f_left_shoulder_abduction = signed_angle(v_arm_left, upward, forward)
        h_left_shoulder_adduction = signed_angle(v_arm_left, forward, upward)
        left_shoulder_extension = signed_angle(v_arm_left, upward, horizontal)
    
        v_arm_right = vector(landmarks[RIGHT_SHOULDER],  landmarks[RIGHT_ELBOW])
        f_right_shoulder_abduction = signed_angle(v_arm_right, upward, forward)
        h_right_shoulder_adduction = signed_angle(v_arm_right, forward, upward)
        right_shoulder_extension = signed_angle(v_arm_right, upward, horizontal)
    
        # Ankle dorsiflexion
        v_shin_left = vector(landmarks[LEFT_KNEE],  landmarks[LEFT_ANKLE])
        left_ankle_dorsiflexion = signed_angle(v_shin_left, upward, horizontal)
    
        v_shin_right = vector(landmarks[RIGHT_KNEE],  landmarks[RIGHT_ANKLE])
        right_ankle_dorsiflexion = signed_angle(v_shin_right, upward, horizontal)
    
        # Left knee valgus indicator
        f_proj_left_hip = vector_plane_project(point(landmarks[LEFT_HIP]), forward)
        f_proj_left_knee = vector_plane_project(point(landmarks[LEFT_KNEE]), forward)
        f_proj_left_ankle = vector_plane_project(point(landmarks[LEFT_ANKLE]), forward)
        left_knee_valgus_indicator = signed_angle(f_proj_left_hip - f_proj_left_knee, 
                                                  f_proj_left_ankle - f_proj_left_knee, 
                                                  forward)
        # Right knee valgus indicator
        f_proj_right_hip = vector_plane_project(point(landmarks[RIGHT_HIP]), forward)
        f_proj_right_knee = vector_plane_project(point(landmarks[RIGHT_KNEE]), forward)
        f_proj_right_ankle = vector_plane_project(point(landmarks[RIGHT_ANKLE]), forward)
        right_knee_valgus_indicator = signed_angle(f_proj_right_hip - f_proj_right_knee, 
                                                   f_proj_right_ankle - f_proj_right_knee, 
                                                   forward)

    angles = {
        'torso flexion': torso_flexion,
        'left elbow': left_elbow,
        'right elbow': right_elbow,
        'left shoulder': left_shoulder,
        'right shoulder': right_shoulder,
        'left wrist': left_wrist,
        'right wrist': right_wrist,
        'left knee': left_knee,
        'right knee': right_knee,
        'left hip': left_hip,
        'right hip': right_hip,
        'left frontal shoulder abduction': f_left_shoulder_abduction,
        'right frontal shoulder abduction': f_right_shoulder_abduction,
        'left scapular upward rotation': left_scapular_upward_rotation,
        'right scapular upward rotation': right_scapular_upward_rotation,
        'left ankle dorsiflexion': left_ankle_dorsiflexion,
        'right ankle dorsiflexion': right_ankle_dorsiflexion,
        'left knee valgus indicator': left_knee_valgus_indicator,
        'right knee valgus indicator': right_knee_valgus_indicator,
        'left horizontal shoulder adduction': h_left_shoulder_adduction,
        'right horizontal shoulder adduction': h_right_shoulder_adduction,
        'left shoulder extension': left_shoulder_extension,
        'right shoulder extension': right_shoulder_extension,
    }

    return angles

def extract_landmarks(data, all_angles, angle_type):
    frames = data['frames']
    angles_per_frame = []
    for frame in frames:
        landmarks = frame['landmarks']
        if landmarks and len(landmarks) >= 33:
            angles_dict = calculate_angles(landmarks, angle_type)
            # Add angles with a fixed order (each angle will have the same order in all sequences)
            angles = [angles_dict[angle] for angle in all_angles]
            angles_per_frame.append(angles)
    if angle_type == 'all':
        angle_indexes = {}
        k = 0
        for a, angle in enumerate(all_angles):
            if isinstance(angles_per_frame[0][a], list):
                angle_indexes[angle] = []
                for j in range(len(angles_per_frame[0][a])):
                    angle_indexes[angle].append(k)
                    k += 1
            else:
                angle_indexes[angle] = [k]
                k += 1
        new_angles_per_frame = np.empty((len(angles_per_frame), k), np.float32)
        k = 0
        for a, angle in enumerate(all_angles):
            if isinstance(angles_per_frame[0][a], list):
                for i in range(len(angles_per_frame)):
                    for j in range(len(angles_per_frame[i][a])):
                        new_angles_per_frame[i, angle_indexes[angle][j]] = angles_per_frame[i][a][j]
            else:
                for i in range(len(angles_per_frame)):
                    new_angles_per_frame[i, angle_indexes[angle][0]] = angles_per_frame[i][a]
        return new_angles_per_frame, angle_indexes
    
    angle_indexes = {}
    for a, angle in enumerate(all_angles):
        angle_indexes[angle] = [a]
    if angle_type == 'unsigned':
        return np.array(angles_per_frame), angle_indexes
    if angle_type == 'signed':
        return np.array(angles_per_frame), angle_indexes
    for a in range(len(all_angles)):
        if isinstance(angles_per_frame[0][a], list):
            if angle_type == 'max-energy':
                values = np.array([frame[a] for frame in angles_per_frame])
                j = np.argmax(np.std(values, axis=0))
                for i in range(len(angles_per_frame)):
                    angles_per_frame[i][a] = values[i, j]
            elif angle_type == 'sagittal':
                for i in range(len(angles_per_frame)):
                    angles_per_frame[i][a] = angles_per_frame[i][a][0]
            elif angle_type == 'coronal':
                for i in range(len(angles_per_frame)):
                    angles_per_frame[i][a] = angles_per_frame[i][a][1]
            elif angle_type == 'transverse':
                for i in range(len(angles_per_frame)):
                    angles_per_frame[i][a] = angles_per_frame[i][a][2]
    return np.array(angles_per_frame), angle_indexes
