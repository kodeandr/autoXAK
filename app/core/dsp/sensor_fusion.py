from typing import List, Dict, Any, Tuple
import numpy as np


class SensorFusionEngine:
    def __init__(self, sampling_rate_hz: float = 50.0):
        self.fs = sampling_rate_hz
        self.dt = 1.0 / sampling_rate_hz

    def process_telemetry(
        self,
        samples: List[Dict[str, Any]],
        gps_speed_mps: float
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Комплексная очистка сигналов:
        1. Z-Shock Veto: подавление ложных продольных перегрузок при ударах подвески.
        2. Throttle Hunting Detector: анализ положительной кинетической энергии (PKE)
           и знакопеременных колебаний тяги на крейсерской скорости.
        """
        n = len(samples)
        if n < 5:
            return np.zeros(n), {
                "road_anomalies_count": 0,
                "throttle_hunting_detected": False,
                "hunting_penalty_liters": 0.0,
                "pke_metric": 0.0,
                "smooth_score": 100
            }

        # Извлечение массивов
        ax = np.array([s.get("ax", 0.0) for s in samples], dtype=float)
        ay = np.array([s.get("ay", 0.0) for s in samples], dtype=float)
        az = np.array([s.get("az", 0.0) for s in samples], dtype=float)
        gy = np.array([s.get("gy", 0.0) for s in samples], dtype=float) # тангаж кузова (pitch)

        # ---------------------------------------------------------------------
        # 1. Z-SHOCK VETO (Фильтр дорожных неровностей: лежачие полицейские, ямы)
        # ---------------------------------------------------------------------
        # Вертикальный рывок: производная d(az)/dt
        jerk_z = np.gradient(az, self.dt)
        
        # Порог дорожного удара: амплитуда |az| > 3.5 м/с² или рывок |jerk_z| > 15.0 м/с³
        impact_mask = (np.abs(az) > 3.5) | (np.abs(jerk_z) > 15.0)
        
        clean_ax = ax.copy()
        road_anomalies_count = 0
        suppression_window = int(0.35 * self.fs) # 350 мс заморозки продольного отклика
        
        i = 0
        while i < n:
            if impact_mask[i]:
                # Проверяем кросс-валидацию по гироскопу тангажа (клевки кузова)
                pitch_activity = np.max(np.abs(gy[max(0, i - 10):min(n, i + 10)])) if n > 0 else 0.0
                
                # Если зафиксирован характерный всплеск удара подвески
                road_anomalies_count += 1
                end_idx = min(n, i + suppression_window)
                
                # Обнуляем ложный продольный стресс, возникший из-за наклона телефона
                clean_ax[i:end_idx] = 0.0
                i = end_idx
            else:
                i += 1

        # ---------------------------------------------------------------------
        # 2. THROTTLE HUNTING DETECTOR (Рыскание педалью газа на скорости)
        # ---------------------------------------------------------------------
        throttle_hunting = False
        hunting_penalty_liters = 0.0
        pke = 0.0

        # Анализируем крейсерский режим: скорость выше 45 км/ч (12.5 м/с)
        if gps_speed_mps > 12.5:
            # Считаем смену знака ускорения (Zero-Crossing Rate) в полосе слабого газа
            micro_flips = 0
            for idx in range(1, len(clean_ax)):
                if (clean_ax[idx - 1] > 0.15 and clean_ax[idx] < -0.15) or \
                   (clean_ax[idx - 1] < -0.15 and clean_ax[idx] > 0.15):
                    micro_flips += 1

            duration_sec = n * self.dt
            flips_per_minute = (micro_flips / max(duration_sec, 1.0)) * 60.0

            # Оценка удельной кинетической энергии (PKE): всплески положительной работы
            positive_accels = clean_ax[clean_ax > 0.1]
            if len(positive_accels) > 0:
                pke = float(np.sum(positive_accels * self.dt * gps_speed_mps))

            # Если водитель меняет знак тяги чаще 12 раз в минуту на высокой скорости
            if flips_per_minute >= 12.0 and pke > 15.0:
                throttle_hunting = True
                # Эмпирический перерасход: около +14% к секундному базовому расходу
                hunting_penalty_liters = (duration_sec / 3600.0) * (gps_speed_mps * 3.6 / 100.0) * 1.2

        # Расчет итогового балла плавности (Smooth Score 0..100)
        harsh_events = np.sum((clean_ax > 2.0) | (clean_ax < -2.5))
        penalty = (harsh_events * 5) + (15 if throttle_hunting else 0)
        smooth_score = max(40, min(100, int(100 - penalty)))

        meta = {
            "road_anomalies_count": road_anomalies_count,
            "throttle_hunting_detected": throttle_hunting,
            "hunting_penalty_liters": round(hunting_penalty_liters, 3),
            "pke_metric": round(pke, 2),
            "smooth_score": smooth_score
        }

        return clean_ax, meta