"""
autoXAK — Сводный запуск всех 5 исследовательских экспериментов
магистерской диссертации НИР 3 (МФТИ, профиль ДИП — Коробов Д. А.).
"""
import os
import sys
from exp1_ved_benchmark import run_ved_benchmark
from exp2_obd_field_audit import run_obd_audit
from exp3_wearengine_tribology import run_wearengine_simulation
from exp4_calibration_iterations import run_calibration_benchmark
from exp5_dsp_performance import run_dsp_benchmark

def main():
    print("=" * 80)
    print("autoXAK: ЗАПУСК ПОЛНОГО КОМПЛЕКСА ИССЛЕДОВАТЕЛЬСКИХ ЭКСПЕРИМЕНТОВ НИР 3")
    print("=" * 80)

    output_dir = "reports"
    os.makedirs(output_dir, exist_ok=True)

    r1 = run_ved_benchmark(output_dir)
    r2 = run_obd_audit("2026-10-30 07-10-44.csv", output_dir)
    r3 = run_wearengine_simulation(output_dir)
    r4 = run_calibration_benchmark(output_dir)
    r5 = run_dsp_benchmark(output_dir)

    print("\n" + "=" * 80)
    print("ИТОГОВАЯ СВОДКА ЭКСПЕРИМЕНТАЛЬНЫХ РЕЗУЛЬТАТОВ (ДЛЯ ОТЧЕТА И ВСТАВКИ В ВКРС):")
    print("=" * 80)
    print(f"1. VED Корпус (28 авто, 1912 поездок):   WAPE = {r1['metrics']['point_wape_percent']}% | Верхняя 95% ДИ = {r1['metrics']['upper_95_bootstrap_ci_percent']}% -> H1 отвергнута (Baseline для CatBoost)")
    print(f"2. Полевой аудит OBD-II (30.02 км):      Корреляция r = {r2['validation_results']['pearson_correlation_r']} | Сдвиг наработки в пробках = {r2['validation_results']['scale_shift_percent']}%")
    print(f"3. WearEngine (Кинетика Аррениуса):      Экономия масла = {r3['economic_comparison']['oil_wear_savings_rub']} ₽ против {r3['economic_comparison']['fuel_savings_rub']} ₽ топлива -> Обоснован пивот")
    print(f"4. Калибровка алгоритма:                 Итерация 1 = {r4['iteration_1_baseline_heuristic']['mape_percent']}% -> Итерация 2 (знаковый тяговый баланс) = {r4['iteration_2_calibrated_physics']['stand_mape_percent']}%")
    print(f"5. Быстродействие и DSP:                 Чанк 60 с = {r5['performance_chunk_processing']['processing_time_ms']} мс (SLA < 50 мс) | Сжатие RDP = {r5['rdp_compression']['compression_ratio_percent']}%")
    print("=" * 80)
    print(f"Все артефакты и JSON-отчеты успешно сохранены в папку: ./{output_dir}/\n")

if __name__ == "__main__":
    main()