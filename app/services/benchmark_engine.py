import numpy as np
from typing import List, Optional
from scipy import stats
from pydantic import BaseModel

class VerificationReport(BaseModel):
    total_points: int
    duration_minutes: float
    obd_ground_truth_hours: float
    autoxak_predicted_hours: float
    mape_percent: Optional[float]
    hypothesis_confirmed: bool
    t_statistic: Optional[float]
    p_value: Optional[float]
    scientific_conclusion: str

class BenchmarkEngine:
    """
    Модуль сравнительной верификации точности безаппаратной модели autoXAK
    относительно эталонных аппаратных данных диагностического сканера OBD2/CAN-шины.
    """
    
    @staticmethod
    def calculate_obd_ground_truth_hours(
        engine_rpm: np.ndarray, 
        engine_load_percent: np.ndarray, 
        oil_temp_c: np.ndarray, 
        dt: float = 0.02
    ) -> float:
        """
        Расчет эталонных моточасов по прямому съему с бортового компьютера (CAN-шина):
        Учитываются фактические обороты коленвала, нагрузка на цилиндропоршневую группу и температура масла.
        """
        arrays = [np.asarray(values, dtype=float) for values in (engine_rpm, engine_load_percent, oil_temp_c)]
        if not np.isfinite(dt) or dt <= 0 or not len(arrays[0]) or any(values.ndim != 1 or len(values) != len(arrays[0]) or not np.isfinite(values).all() for values in arrays):
            raise ValueError("OBD signals must be nonempty, finite, aligned, with positive dt")
        engine_rpm, engine_load_percent, oil_temp_c = arrays
        if (engine_rpm < 0).any() or (engine_rpm > 20000).any() or (engine_load_percent < 0).any() or (engine_load_percent > 100).any() or (oil_temp_c < -50).any() or (oil_temp_c > 250).any():
            raise ValueError("OBD values are outside their physical ranges")
        # Базовый норматив холостого хода: 800 RPM
        rpm_factor = engine_rpm / 800.0
        load_factor = 1.0 + (engine_load_percent / 100.0) * 0.5
        
        # Термический множитель масла по датчику температуры в картере
        temp_factor = np.where(oil_temp_c > 105.0, 1.3, 1.0)
        temp_factor = np.where(oil_temp_c < 70.0, 1.2, temp_factor)

        equivalent_seconds = dt * rpm_factor * load_factor * temp_factor
        return float(np.sum(equivalent_seconds) / 3600.0)

    def evaluate_experiment(
        self, 
        autoxak_engine_hours: float, 
        obd_rpm: List[float], 
        obd_load: List[float], 
        obd_temp: List[float],
        user_savings: List[float],
        dt: float = 0.02
    ) -> VerificationReport:
        if not np.isfinite(autoxak_engine_hours) or autoxak_engine_hours < 0:
            raise ValueError("Predicted hours must be nonnegative and finite")
        rpm_arr = np.array(obd_rpm, dtype=np.float64)
        load_arr = np.array(obd_load, dtype=np.float64)
        temp_arr = np.array(obd_temp, dtype=np.float64)

        # 1. Расчет аппаратного эталона (Ground Truth)
        ground_truth_hours = self.calculate_obd_ground_truth_hours(rpm_arr, load_arr, temp_arr, dt)

        # 2. Расчет относительной погрешности MAPE
        if ground_truth_hours > 0:
            mape = abs(ground_truth_hours - autoxak_engine_hours) / ground_truth_hours * 100.0
        else:
            mape = None

        # Критерий подтверждения гипотезы из паспорта ВКРС МФТИ: MAPE <= 10.0%
        hypothesis_confirmed = mape is not None and mape <= 10.0

        # 3. Статистический парный t-тест на экономию (проверка отличия от нуля)
        savings_arr = np.array(user_savings, dtype=np.float64)
        if not np.isfinite(savings_arr).all():
            raise ValueError("Savings must be finite")
        if len(savings_arr) >= 5 and np.std(savings_arr) > 0:
            t_stat, p_val = stats.ttest_1samp(savings_arr, popmean=0.0)
        else:
            t_stat, p_val = None, None

        if mape is None:
            conclusion = "Погрешность не определена: эталонная наработка равна нулю. Гипотеза не подтверждена."
        else:
            conclusion = f"Погрешность модели {mape:.2f}%; гипотеза H1 {'подтверждена' if hypothesis_confirmed else 'не подтверждена'} (порог 10%)."
        if p_val is None:
            conclusion += " Недостаточно данных для t-теста экономии."
        else:
            conclusion += f" p-value = {p_val:.4f}; статистическая значимость {'есть' if p_val < 0.05 else 'не подтверждена'}."

        return VerificationReport(
            total_points=len(obd_rpm),
            duration_minutes=round((len(obd_rpm) * dt) / 60.0, 2),
            obd_ground_truth_hours=round(ground_truth_hours, 5),
            autoxak_predicted_hours=round(autoxak_engine_hours, 5),
            mape_percent=round(mape, 2) if mape is not None else None,
            hypothesis_confirmed=hypothesis_confirmed,
            t_statistic=round(float(t_stat), 3) if t_stat is not None else None,
            p_value=round(float(p_val), 5) if p_val is not None else None,
            scientific_conclusion=conclusion
        )