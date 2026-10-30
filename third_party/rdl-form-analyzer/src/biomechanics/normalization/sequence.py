
# Normalization of the whole sequence (T frames) for RDL analysis and comparison with PM-Ideal.
# Input: kps_xy_clean (or raw) from full_analysis; output: dict with kps_xy_normalized and masks.

from __future__ import annotations
# Permite usar "NormalizationConfig | None" como anotación de tipo sin problemas de versión de Python.

from typing import Any
# "Any" = "puede ser cualquier tipo". Se usa en el dict de salida, que mezcla arrays, strings, números...

import numpy as np

from src.biomechanics.normalization.config import NormalizationConfig
from src.biomechanics.normalization.validation import validate_keypoints_sequence_2d


def normalize_pose_sequence(
    kps_xy: np.ndarray,
    # Entrada: pose (limpia o raw), forma (T, 17, 2) -> T frames, 17 keypoints, 2 coords (x,y).
    *,
    # Todo lo que va después del "*" hay que pasarlo por nombre, ej: normalize_pose_sequence(kps, config=cfg).
    config: NormalizationConfig | None = None,
    # Configuración opcional; si no se pasa (queda en None), se usan los valores por defecto.
) -> dict[str, Any]:
    """Places each frame in coordinates relative to the hip and scales by the torso.

    Per valid frame: origin = right hip (12), reference length = |shoulder(6)−hip|.
    fixed_median mode (the pipeline's): a single scale = median of those lengths across the video
    (robust to a frame with a poorly detected torso; same rationale as in orientation.py).
    Formula: (point − hip) / fixed_scale → shoulder–hip ≈ 1, hip at (0,0).

    Returns kps_xy_normalized, mask_valid_normalized, origins, scales, raw_scales,
    valid_frame_count/ratio and warnings.
    build_rdl_analysis_context and the detectors consume user_pose_sequence_normalization.
    """
    # --- PASO 1: preparar la configuración y validar que sea soportada ---
    cfg = config or NormalizationConfig()
    if cfg.method != "pelvis_torso_scale":
        raise ValueError(f"Unsupported normalization method: {cfg.method!r}.")
    if cfg.apply_rotation:
        raise NotImplementedError(
            "Rotation-based normalization is not implemented because the default pipeline preserves orientation differences."
        )

    mode = str(cfg.sequence_scale_mode)
    if mode not in {"fixed_median", "framewise"}:
        raise ValueError(
            f"Unsupported sequence_scale_mode: {mode!r}. Expected 'fixed_median' or 'framewise'."
        )

    # --- PASO 2: validar la forma del array de entrada y sacar parámetros sueltos ---
    arr = validate_keypoints_sequence_2d(kps_xy, expected_keypoints=17)

    pelvis_idx = int(cfg.pelvis_idx)     
    shoulder_idx = int(cfg.shoulder_idx)  
    eps = float(cfg.eps)                
    total_frame_count = int(arr.shape[0])

    # --- PASO 3: comprobar que los índices de la config son válidos ---
    if pelvis_idx < 0 or pelvis_idx >= arr.shape[1]:
        raise ValueError(f"pelvis_idx out of range: {pelvis_idx}")
    if shoulder_idx < 0 or shoulder_idx >= arr.shape[1]:
        raise ValueError(f"shoulder_idx out of range: {shoulder_idx}")

    # --- PASO 4: crear "moldes vacíos" (rellenos de nan / False) para ir llenando después ---
    origins = np.full((total_frame_count, 2), np.nan, dtype=np.float64)
    # Tabla (T filas, 2 columnas) -> posición [x,y] de la cadera por frame. Todo nan al principio.
    raw_scales = np.full((total_frame_count,), np.nan, dtype=np.float64)
    # Lista (T,) -> longitud del torso "cruda" (sin normalizar) por frame. Todo nan al principio.
    scales = np.full((total_frame_count,), np.nan, dtype=np.float64)
    # Lista (T,) -> escala FINAL usada para normalizar cada frame. Todo nan al principio.
    normalized = np.full_like(arr, np.nan, dtype=np.float64)
    # Array con la MISMA forma que arr (T, 17, 2) -> aquí irá la pose ya centrada y escalada. Todo nan al principio.
    mask_valid_normalized = np.zeros((total_frame_count,), dtype=bool)
    # Lista (T,) de True/False -> "¿este frame quedó bien normalizado?". Todo False al principio.
    warnings: list[str] = []
    # Lista normal de Python (no NumPy), vacía -> aquí se guardan mensajes de aviso si algo va mal.

    # --- PASO 5: sacar cadera y hombro de TODOS los frames a la vez, y comprobar validez ---
    pelvis_pts = arr[:, pelvis_idx, :]     # forma (T, 2): [x,y] de la cadera en cada frame
    shoulder_pts = arr[:, shoulder_idx, :] # forma (T, 2): [x,y] del hombro en cada frame

    pelvis_finite = np.isfinite(pelvis_pts).all(axis=1)
    # Para cada frame: ¿tanto x como y de la cadera son números válidos (no nan)? -> (T,) de True/False.
    # .all(axis=1) exige que AMBAS coordenadas sean válidas para que el frame cuente como válido.
    shoulder_finite = np.isfinite(shoulder_pts).all(axis=1)
    # Lo mismo pero para el hombro.

    torso_lengths = np.linalg.norm(shoulder_pts - pelvis_pts, axis=1)
    # Vector hombro-cadera (resta) y su longitud (distancia), frame a frame -> (T,) de números.

    mask_valid_candidate = pelvis_finite & shoulder_finite & (torso_lengths > eps)
    # Un frame es "candidato válido" solo si: cadera OK Y hombro OK Y el torso no mide ~0.
    # "&" combina las tres listas de booleanos posición a posición (no es el "and" normal de Python).

    # --- PASO 6: rellenar origins/raw_scales SOLO en los frames válidos (el resto se queda en nan) ---
    origins[mask_valid_candidate] = pelvis_pts[mask_valid_candidate]
    # "De pelvis_pts, coge solo las filas donde la máscara es True, y cópialas en esas mismas
    # posiciones de origins." Es un filtro + copia en una sola línea (equivale a un for+if).
    raw_scales[mask_valid_candidate] = torso_lengths[mask_valid_candidate]
    # Mismo patrón, para las longitudes de torso.

    # --- PASO 7: calcular la escala según el modo elegido ---
    if mode == "fixed_median":
        # Modo por defecto del pipeline: UNA sola escala para todo el vídeo.

        valid_scales = raw_scales[np.isfinite(raw_scales) & (raw_scales > eps)]
        # De raw_scales (que tiene nan en los frames inválidos), extrae SOLO los números reales y > 0.
        # valid_scales es más corto que raw_scales: no contiene ningún nan.

        if valid_scales.size == 0:
            # Si NINGÚN frame fue válido, no hay nada que normalizar: aviso y salida temprana.
            warnings.append("NO_VALID_REFERENCE_SEGMENTS_FOR_SEQUENCE_NORMALIZATION")
            return {
                "kps_xy_normalized": normalized,       # se devuelve tal cual, todo nan
                "mask_valid_normalized": mask_valid_normalized,  # todo False
                "origins": origins,
                "scales": scales,
                "raw_scales": raw_scales,
                "method": "pelvis_torso_scale",
                "sequence_scale_mode": mode,
                "pelvis_idx": pelvis_idx,
                "shoulder_idx": shoulder_idx,
                "rotation_applied": False,
                "valid_frame_count": 0,
                "total_frame_count": total_frame_count,
                "valid_frame_ratio": 0.0,
                "warnings": warnings,
            }

        fixed_scale = float(np.median(valid_scales))
        # La escala del vídeo entero = mediana de las longitudes de torso válidas.
        # Se usa mediana en vez de media porque es más resistente a un frame con una medida rara (outlier).

        if not np.isfinite(fixed_scale) or fixed_scale <= eps:
            # Comprobación de seguridad extra: si la mediana saliera mal (nan o ~0), avisa y sal.
            warnings.append("INVALID_FIXED_MEDIAN_SCALE_FOR_SEQUENCE_NORMALIZATION")
            return {
                "kps_xy_normalized": normalized,
                "mask_valid_normalized": mask_valid_normalized,
                "origins": origins,
                "scales": scales,
                "raw_scales": raw_scales,
                "method": "pelvis_torso_scale",
                "sequence_scale_mode": mode,
                "pelvis_idx": pelvis_idx,
                "shoulder_idx": shoulder_idx,
                "rotation_applied": False,
                "valid_frame_count": 0,
                "total_frame_count": total_frame_count,
                "valid_frame_ratio": 0.0,
                "warnings": warnings,
            }

        # Cálculo principal: centrar en la cadera y escalar, para todos los frames válidos a la vez.
        normalized[mask_valid_candidate] = (
            arr[mask_valid_candidate] - origins[mask_valid_candidate][:, None, :]
            # arr[mask_valid_candidate]: solo los frames válidos, forma (N_validos, 17, 2).
            # origins[mask_valid_candidate]: cadera de esos mismos frames, forma (N_validos, 2).
            # [:, None, :]: añade un hueco -> (N_validos, 1, 2), para que el broadcasting pueda
            # "repetir" esa cadera a lo largo de los 17 keypoints de cada frame.
            # La resta deja cada frame centrado en su propia cadera (cadera -> [0,0]).
        ) / fixed_scale
        # Divide TODO entre el mismo número (la mediana) -> misma escala para todo el vídeo.

        scales[mask_valid_candidate] = fixed_scale
        # Guarda qué escala se usó en cada frame válido (aquí, siempre la misma: fixed_scale).

        mask_valid_normalized = mask_valid_candidate.copy()
        # De momento, "válido normalizado" = "era válido candidato". Puede recortarse más abajo si
        # algún frame no pasa los tests de sanidad (PASO 8). .copy() evita que ambas variables
        # apunten al MISMO array en memoria (si no, modificar una modificaría la otra sin querer).

    elif mode == "framewise":
        # Modo alternativo: cada frame usa SU PROPIA longitud de torso como escala (menos estable).

        valid_idx = np.where(mask_valid_candidate)[0]
        # En vez de una máscara de True/False, aquí saca los NÚMEROS de posición de los frames válidos.
        # Ej: si mask_valid_candidate = [True, False, True], valid_idx = [0, 2].

        if valid_idx.size > 0:
            normalized[valid_idx] = (
                arr[valid_idx] - origins[valid_idx][:, None, :]
            ) / raw_scales[valid_idx][:, None, None]
            # Mismo centrado que en fixed_median, pero divide cada frame por SU PROPIA escala
            # (raw_scales[valid_idx]), no por una mediana común. El [:, None, None] añade DOS
            # huecos para que ese array de forma (N_validos,) se pueda repetir tanto a lo largo
            # de los 17 keypoints como de las 2 coordenadas (x,y) de cada uno.
            scales[valid_idx] = raw_scales[valid_idx]
            mask_valid_normalized[valid_idx] = True

    # --- PASO 8: test de sanidad 1 - la cadera normalizada debe quedar en (0,0) ---
    if np.any(mask_valid_normalized):
        # np.any(...) = ¿hay AL MENOS un True en la lista? Si no hay ningún frame válido, no sigue.

        pelvis_norm = normalized[mask_valid_normalized, pelvis_idx, :]
        # De los frames marcados como válidos, saca la posición de la cadera YA normalizada.

        pelvis_ok = np.all(np.isclose(pelvis_norm, 0.0, atol=1e-6), axis=1)
        # np.isclose(x, 0.0): ¿es x "prácticamente" 0? (con margen de error atol=1e-6, por redondeos
        # de coma flotante). .all(axis=1): tanto x como y tienen que ser ~0 para que el frame pase.

        if not np.all(pelvis_ok):
            # Si ALGÚN frame no tiene la cadera en (0,0) (indicaría un bug), se invalida ese frame:
            fail_idx = np.where(mask_valid_normalized)[0][~pelvis_ok]
            # "~pelvis_ok" invierte los booleanos (True <-> False) -> selecciona los que FALLARON.
            mask_valid_normalized[fail_idx] = False
            normalized[fail_idx] = np.nan
            scales[fail_idx] = np.nan
            warnings.append("NORMALIZED_PELVIS_NOT_ZERO")

    # --- PASO 9: test de sanidad 2 (solo en modo framewise) - hombro-cadera debe medir ~1.0 ---
    if mode == "framewise" and np.any(mask_valid_normalized):
        valid_idx = np.where(mask_valid_normalized)[0]
        shoulder_norm = normalized[valid_idx, shoulder_idx, :]
        pelvis_norm = normalized[valid_idx, pelvis_idx, :]
        dists = np.linalg.norm(shoulder_norm - pelvis_norm, axis=1)
        dist_ok = np.isclose(dists, 1.0, atol=1e-6)
        if not np.all(dist_ok):
            fail_idx = valid_idx[~dist_ok]
            mask_valid_normalized[fail_idx] = False
            normalized[fail_idx] = np.nan
            scales[fail_idx] = np.nan
            warnings.append("NORMALIZED_TORSO_SCALE_NOT_ONE")
        # En modo fixed_median este test no hace falta: al usar la MISMA escala para todos los
        # frames, no puede pasar que un frame concreto mida "mal" por tener su propia escala.

    # --- PASO 10: limpiar avisos duplicados y calcular estadísticas finales ---
    if warnings:
        warnings = list(dict.fromkeys(warnings))
        # Truco para quitar duplicados de una lista MANTENIENDO el orden original.
        # (un set() normal quitaría duplicados pero desordenaría la lista)

    valid_frame_count = int(np.count_nonzero(mask_valid_normalized))
    # Cuenta cuántos True hay en la máscara final (cuántos frames quedaron bien normalizados).

    valid_frame_ratio = float(valid_frame_count / total_frame_count) if total_frame_count > 0 else 0.0
    # Porcentaje (en decimal, de 0 a 1) de frames válidos sobre el total. El "if > 0" evita
    # dividir entre cero si el vídeo no tuviera frames.

    # --- PASO 11: devolver todo empaquetado en un diccionario ---
    return {
        "kps_xy_normalized": normalized,
        "mask_valid_normalized": mask_valid_normalized,
        "origins": origins,
        "scales": scales,
        "raw_scales": raw_scales,
        "method": "pelvis_torso_scale",
        "sequence_scale_mode": mode,
        "pelvis_idx": pelvis_idx,
        "shoulder_idx": shoulder_idx,
        "rotation_applied": False,
        "valid_frame_count": valid_frame_count,
        "total_frame_count": total_frame_count,
        "valid_frame_ratio": valid_frame_ratio,
        "warnings": warnings,
    }
