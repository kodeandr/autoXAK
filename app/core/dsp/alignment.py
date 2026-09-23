import numpy as np
from typing import Tuple, Dict, Any


class IMUAlignmentService:
    """
    Двухфазная автокалибровка матрицы ориентации смартфона относительно кузова автомобиля.
    Преобразует произвольный базис смартфона в стандартизированный ISO 8855 базис ТС.
    """

    def __init__(self, sample_rate_hz: float = 50.0):
        self.dt = 1.0 / sample_rate_hz
        self.g_val = 9.80665

    def calibrate_and_transform(
        self,
        raw_ax: np.ndarray,
        raw_ay: np.ndarray,
        raw_az: np.ndarray,
        speeds_mps: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, Dict[str, Any]]:
        n_samples = len(raw_ax)
        if n_samples < 50:
            return raw_ax, raw_ay, raw_az - self.g_val, {"pitch_deg": 0.0, "roll_deg": 0.0, "is_aligned": False}

        raw_signals = np.column_stack((raw_ax, raw_ay, raw_az))

        # --- 1. Определение вертикали (Gravity Vector) ---
        # Покой: СКОРОСТЬ < 0.4 м/с И УСКОРЕНИЕ < 0.2 м/с² ОДНОВРЕМЕННО
        gps_diff = np.diff(speeds_mps, prepend=speeds_mps[0]) / self.dt
        static_mask = (speeds_mps < 0.4) & (np.abs(gps_diff) < 0.20)

        if np.sum(static_mask) >= 15:
            g_estimate = np.mean(raw_signals[static_mask], axis=0)
        else:
            # Если остановок не было (трасса) — ищем участки движения с постоянной скоростью
            quasi_mask = np.abs(gps_diff) < 0.15
            if np.sum(quasi_mask) >= 20:
                g_estimate = np.median(raw_signals[quasi_mask], axis=0)
            else:
                g_estimate = np.median(raw_signals, axis=0)

        g_norm = np.linalg.norm(g_estimate)
        if g_norm < 1e-3:
            g_estimate = np.array([0.0, 0.0, self.g_val])
            g_norm = self.g_val

        # Нормированная вертикальная ось Z кузова (против вектора g)
        z_veh = g_estimate / g_norm

        # Монтажные углы наклона устройства (Pitch / Roll)
        pitch_rad = np.arctan2(z_veh[0], np.sqrt(z_veh[1]**2 + z_veh[2]**2))
        roll_rad = np.arctan2(-z_veh[1], z_veh[2])

        # Вычитаем физический вектор гравитации заданной величины (9.80665 м/с²)
        g_vector = z_veh * self.g_val
        dyn_signals = raw_signals - g_vector

        # Проекция на плоскость дороги
        dots = np.sum(dyn_signals * z_veh, axis=1, keepdims=True)
        horiz_signals = dyn_signals - dots * z_veh

        # --- 2. Определение направления движения (Forward Vector) ---
        accel_mask = (gps_diff > 0.3) & (speeds_mps > 0.4)

        if np.sum(accel_mask) >= 10:
            weights = gps_diff[accel_mask, np.newaxis]
            fwd_estimate = np.sum(horiz_signals[accel_mask] * weights, axis=0)
            fwd_norm = np.linalg.norm(fwd_estimate)
            if fwd_norm > 1e-4:
                x_veh = fwd_estimate / fwd_norm
            else:
                x_veh = self._fallback_orthogonal(z_veh)
        else:
            x_veh = self._fallback_orthogonal(z_veh)

        # --- 3. Ортонормализация базиса ISO 8855 ---
        y_veh = np.cross(z_veh, x_veh)
        y_norm = np.linalg.norm(y_veh)
        if y_norm < 1e-4:
            y_veh = np.array([0.0, 1.0, 0.0])
        else:
            y_veh /= y_norm

        x_veh = np.cross(y_veh, z_veh)
        x_veh /= np.linalg.norm(x_veh)

        # Матрица перехода R = [x_veh, y_veh, z_veh]
        R = np.column_stack((x_veh, y_veh, z_veh))

        # Преобразование динамических ускорений в базис кузова ТС
        body_accel = np.dot(dyn_signals, R)

        a_long = body_accel[:, 0]
        a_lat = body_accel[:, 1]
        a_vert = body_accel[:, 2]

        meta = {
            "pitch_deg": round(float(np.degrees(pitch_rad)), 1),
            "roll_deg": round(float(np.degrees(roll_rad)), 1),
            "alignment_confidence": round(float(min(1.0, np.sum(accel_mask) / 30.0)), 2),
            "is_aligned": True
        }

        return a_long, a_lat, a_vert, meta

    def _fallback_orthogonal(self, z_vec: np.ndarray) -> np.ndarray:
        arbitrary = np.array([1.0, 0.0, 0.0]) if abs(z_vec[0]) < 0.8 else np.array([0.0, 1.0, 0.0])
        ortho = arbitrary - np.dot(arbitrary, z_vec) * z_vec
        norm = np.linalg.norm(ortho)
        return ortho / norm if norm > 1e-4 else np.array([1.0, 0.0, 0.0])
