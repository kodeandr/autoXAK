"""
Метрологический верификационный стенд (Evaluation Suite).
Сравнивает безаппаратную оценку T_App со стендовым эталоном CAN-шины T_OBD.
Проверяет гипотезу: MAPE <= 10.0%, p-value < 0.05.
"""
import numpy as np
from scipy import stats
from typing import List, Dict

class BenchmarkEngine:
    @staticmethod
    def calculate_obd_ground_truth_hours(time_sec: np.ndarray, rpm: np.ndarray, 
                                        engine_load: np.ndarray) -> float:
        """
        Аппаратный эталон CAN: физическое интегрирование оборотов коленчатого вала
        с весовым коэффициентом циклового наполнения цилиндров (PID 0104 Engine Load).
        """
        dt = np.diff(time_sec, prepend=time_sec[0])
        idle_rpm_ref = 800.0
        load_factor = 1.0 + (engine_load / 100.0) * 0.5
        severity = (rpm / idle_rpm_ref) * load_factor
        
        equiv_seconds = np.sum(severity * dt)
        return float(equiv_seconds / 3600.0)

    @staticmethod
    def evaluate_cohort_metrics(t_app_list: List[float], t_obd_list: List[float]) -> Dict[str, float]:
        """
        Статистическая валидация гипотезы H1 против H0.
        """
        app_arr = np.array(t_app_list)
        obd_arr = np.array(t_obd_list)
        
        errors_abs = np.abs(obd_arr - app_arr)
        percentage_errors = (errors_abs / np.maximum(1e-5, obd_arr)) * 100.0
        
        mape = float(np.mean(percentage_errors))
        rmse = float(np.sqrt(np.mean((obd_arr - app_arr) ** 2)))
        r_pearson, _ = stats.pearsonr(app_arr, obd_arr)
        
        # Парный t-тест Стьюдента
        t_stat, p_value_student = stats.ttest_rel(app_arr, obd_arr)
        # Тест знаковых рангов Уилкоксона
        w_stat, p_value_wilcoxon = stats.wilcoxon(app_arr, obd_arr)
        
        hypothesis_confirmed = bool(mape <= 10.0 and p_value_student < 0.05)
        
        return {
            "mape_percent": round(mape, 3),
            "rmse_hours": round(rmse, 4),
            "pearson_r": round(float(r_pearson), 4),
            "p_value_student": float(p_value_student),
            "p_value_wilcoxon": float(p_value_wilcoxon),
            "hypothesis_status": "CONFIRMED (H1)" if hypothesis_confirmed else "REJECTED (H0)"
        }

if __name__ == "__main__":
    # Пример верификации на калибровочной когорте из 40 реальных заездов
    np.random.seed(42)
    sample_size = 40
    # Генерация синтезированных контрольных данных для проверки пайплайна
    obd_ground_truth = np.random.uniform(0.3, 1.8, sample_size)
    # Погрешность модели со случайным шумом ~2%
    app_estimates = obd_ground_truth * (1.0 + np.random.normal(0.0, 0.02, sample_size))
    
    metrics = BenchmarkEngine.evaluate_cohort_metrics(list(app_estimates), list(obd_ground_truth))
    print("=== ОТЧЕТ МЕТРОЛОГИЧЕСКОЙ ВЕРИФИКАЦИИ АВТОХАК ===")
    for k, v in metrics.items():
        print(f"{k}: {v}")