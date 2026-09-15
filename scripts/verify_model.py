import os
import json
import argparse
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d

# Базовые константы калибровки для силовых агрегатов TGDI / TSI
RPM_IDLE_NOMINAL = 800.0   # Номинальные обороты холостого хода прогретого ДВС (об/мин)
RPM_REDLINE = 6000.0       # Номинальная отсечка по оборотам

def load_mobile_telemetry(json_path: str) -> pd.DataFrame:
    """Загрузка и нормализация сессии мобильного логгера autoXAK."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    stream = data.get("telemetry_stream") or data.get("samples")
    if not stream:
        raise ValueError(f"В файле {json_path} не найден массив телеметрии!")
        
    df = pd.DataFrame(stream)
    
    # Нормализация наименования временной оси
    if "t" not in df.columns and "timestamp" in df.columns:
        df.rename(columns={"timestamp": "t"}, inplace=True)
    elif "t" not in df.columns and "timestamp_ms" in df.columns:
        df["t"] = df["timestamp_ms"] / 1000.0
        
    df["t_rel"] = df["t"] - df["t"].iloc[0]
    return df

def load_obd_log(csv_path: str) -> pd.DataFrame:
    """Парсинг экспорта CSV из Car Scanner ELM OBD2."""
    # Автоопределение разделителя (запятая или точка с запятой)
    try:
        df = pd.read_csv(csv_path, sep=";")
        if len(df.columns) < 2:
            df = pd.read_csv(csv_path, sep=",")
    except Exception:
        df = pd.read_csv(csv_path, sep=None, engine="python")

    # Нормализация имен колонок под стандарты OBD-II PIDs
    col_mapping = {}
    for col in df.columns:
        c_lower = str(col).lower()
        if "rpm" in c_lower or "обороты" in c_lower:
            col_mapping[col] = "rpm"
        elif "speed" in c_lower or "скорость" in c_lower:
            col_mapping[col] = "obd_speed"
        elif "load" in c_lower or "нагрузка" in c_lower:
            col_mapping[col] = "engine_load"
        elif "time" in c_lower or "время" in c_lower:
            col_mapping[col] = "t"

    df.rename(columns=col_mapping, inplace=True)
    
    if "rpm" not in df.columns:
        raise ValueError(f"В OBD CSV-файле не найдена колонка RPM! Найдены колонки: {list(df.columns)}")
        
    if "t" in df.columns:
        if df["t"].dtype == object:
            df["t"] = pd.to_datetime(df["t"]).astype("int64") / 1e9
        df["t_rel"] = df["t"] - df["t"].iloc[0]
    else:
        # Резервная шкала времени при отсутствии метки (шаг 100 мс / 10 Гц)
        df["t_rel"] = np.linspace(0, len(df) * 0.1, len(df))
        
    return df

def run_evaluation(mobile_json: str, obd_csv: str, output_dir: str = "reports"):
    """Сквозной расчет расхождения модели и эталона с генерацией отчета."""
    os.makedirs(output_dir, exist_ok=True)
    
    print(f"[*] Загрузка логов:\n    Смартфон: {mobile_json}\n    OBD2:     {obd_csv}")
    df_phone = load_mobile_telemetry(mobile_json)
    df_obd = load_obd_log(obd_csv)
    
    # 1. Приведение к общей временной сетке (50 Гц, dt = 0.02 с)
    duration = min(df_phone["t_rel"].max(), df_obd["t_rel"].max())
    common_time = np.arange(0, duration, 0.02)
    dt = 0.02
    
    # Интерполяция эталонных оборотов коленвала с CAN-шины
    f_rpm = interp1d(df_obd["t_rel"], df_obd["rpm"], kind="linear", fill_value="extrapolate")
    rpm_interp = np.clip(f_rpm(common_time), 0.0, RPM_REDLINE)
    
    # Интерполяция параметров кинематики со смартфона
    f_speed = interp1d(df_phone["t_rel"], df_phone["speed"], kind="linear", fill_value="extrapolate")
    f_ax = interp1d(df_phone["t_rel"], df_phone["ax"], kind="linear", fill_value="extrapolate")
    f_ay = interp1d(df_phone["t_rel"], df_phone["ay"], kind="linear", fill_value="extrapolate")
    
    speed_interp = f_speed(common_time)
    ax_interp = f_ax(common_time)
    ay_interp = f_ay(common_time)
    
    # 2. Расчет эталонных эквивалентных моточасов по OBD-II (Ground Truth)
    # Формула наработки: интеграл взвешенной частоты вращения
    rpm_weights = np.where(rpm_interp > 0, rpm_interp / RPM_IDLE_NOMINAL, 0.0)
    # Повышенный коэффициент деградации масла при оборотах > 3000 об/мин (термический стресс)
    high_load_mask = rpm_interp > 3000.0
    rpm_weights[high_load_mask] *= 1.3
    cum_hours_obd = np.cumsum(rpm_weights * dt) / 3600.0
    
    # 3. Расчет безаппаратной модели смартфона autoXAK
    # Оценка стоянки/холостого хода (скорость < 1.5 км/ч) и динамических перегрузок
    k_idle = np.where(speed_interp < 1.5, 1.0, 0.7)
    horiz_acc = np.sqrt(ax_interp**2 + ay_interp**2)
    k_dyn = np.where(horiz_acc > 1.8, (horiz_acc / 1.8) * 1.35, 0.0)
    phone_weights = k_idle + k_dyn
    cum_hours_phone = np.cumsum(phone_weights * dt) / 3600.0
    
    # 4. Расчет статистических критериев (отсекаем первые 2 секунды переходного процесса)
    eval_idx = int(2.0 / dt)
    y_true = cum_hours_obd[eval_idx:]
    y_pred = cum_hours_phone[eval_idx:]
    
    mape = float(np.mean(np.abs((y_true - y_pred) / (y_true + 1e-9))) * 100.0)
    rmse = float(np.sqrt(np.mean((y_true - y_pred)**2)))
    r_matrix = np.corrcoef(y_true, y_pred)
    pearson_r = float(r_matrix[0, 1]) if r_matrix.shape == (2, 2) else 1.0
    
    is_hypothesis_confirmed = bool(mape <= 10.0)
    
    # 5. Экспорт отчета в JSON
    report = {
        "evaluation_status": "SUCCESS",
        "duration_seconds": round(float(duration), 2),
        "total_samples": len(common_time),
        "metrics": {
            "mape_percent": round(mape, 2),
            "rmse_hours": round(rmse, 6),
            "pearson_correlation": round(pearson_r, 4)
        },
        "hypothesis_target": "MAPE <= 10.0%",
        "is_hypothesis_confirmed": is_hypothesis_confirmed,
        "summary": {
            "obd_final_hours": round(float(cum_hours_obd[-1]), 5),
            "phone_final_hours": round(float(cum_hours_phone[-1]), 5),
            "delta_percent": round(float(abs(cum_hours_obd[-1] - cum_hours_phone[-1]) / (cum_hours_obd[-1] + 1e-9) * 100.0), 2)
        }
    }
    
    report_path = os.path.join(output_dir, "validation_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    print("\n==================== ИТОГИ ВЕРИФИКАЦИИ ====================")
    print(f" Длительность сессии:          {report['duration_seconds']} с")
    print(f" Расхождение MAPE:             {report['metrics']['mape_percent']}% (Критерий: <= 10%)")
    print(f" Корреляция Пирсона (r):       {report['metrics']['pearson_correlation']}")
    print(f" Статус гипотезы H1:           {'[ПОДТВЕРЖДЕНА]' if is_hypothesis_confirmed else '[НЕ ПОДТВЕРЖДЕНА]'}")
    print(f" Файл отчета:                  {report_path}")
    print("===========================================================\n")
    
    # 6. Построение графика сходимости (Vertical Layout, 300 DPI)
    fig, ax = plt.subplots(figsize=(10, 6), dpi=300)
    ax.plot(common_time, cum_hours_obd * 60, label="CAN-шина OBD-II (Ground Truth: RPM)", color="#1f77b4", linewidth=2.2)
    ax.plot(common_time, cum_hours_phone * 60, label="autoXAK Model (Смартфон: IMU + GPS)", color="#2ca02c", linestyle="--", linewidth=2.0)
    
    ax.set_title(f"Сходимость оценки эквивалентных моточасов (MAPE = {mape:.2f}%)", fontsize=13, fontweight="bold", pad=12)
    ax.set_xlabel("Время сессии, с", fontsize=11)
    ax.set_ylabel("Эквивалентные моточасы, мин", fontsize=11)
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(fontsize=11, loc="upper left")
    
    box_text = f"MAPE: {mape:.2f}%\nPearson r: {pearson_r:.4f}\nГипотеза: {'ПОДТВЕРЖДЕНА' if is_hypothesis_confirmed else 'ОТКЛОНЕНА'}"
    ax.text(0.70, 0.15, box_text, transform=ax.transAxes, fontsize=10,
            verticalalignment="center", bbox=dict(boxstyle="round,pad=0.5", fc="white", ec="#aaa", lw=1.2))
    
    fig_path = os.path.join(output_dir, "validation_convergence.png")
    plt.tight_layout()
    plt.savefig(fig_path)
    plt.close()
    print(f"[*] График сохранен: {fig_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Стенд верификации моделей autoXAK")
    parser.add_argument("--phone", required=True, help="Путь к JSON-файлу сессии смартфона")
    parser.add_argument("--obd", required=True, help="Путь к CSV-файлу экспорта Car Scanner")
    parser.add_argument("--out", default="reports", help="Папка сохранения результатов")
    args = parser.parse_args()
    
    run_evaluation(args.phone, args.obd, args.out)
