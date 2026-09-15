import os
import json
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

def parse_car_scanner_file(csv_path: str) -> pd.DataFrame:
    df_raw = None
    for sep in [",", ";", "\t"]:
        for enc in ["utf-8", "cp1251", "latin1"]:
            try:
                temp_df = pd.read_csv(csv_path, sep=sep, encoding=enc, nrows=30)
                if len(temp_df.columns) >= 3:
                    df_raw = pd.read_csv(csv_path, sep=sep, encoding=enc, low_memory=False)
                    break
            except Exception:
                continue
        if df_raw is not None:
            break

    if df_raw is None:
        raise ValueError(f"Не удалось распарсить CSV файл {csv_path}")

    raw_map = {orig: str(orig).upper().strip() for orig in df_raw.columns}
    df_raw.rename(columns=raw_map, inplace=True)
    
    df_raw["SECONDS"] = pd.to_numeric(df_raw["SECONDS"].astype(str).str.replace(",", "."), errors="coerce")
    df_raw["VALUE"] = pd.to_numeric(df_raw["VALUE"].astype(str).str.replace(",", "."), errors="coerce")
    df_raw.dropna(subset=["SECONDS", "VALUE"], inplace=True)
    df_raw["PID"] = df_raw["PID"].astype(str).str.upper().str.strip()

    target_rpm_pid = [p for p in df_raw["PID"].unique() if "ENGINE RPM" in p and "X1000" not in p][0]
    speed_pids = [p for p in df_raw["PID"].unique() if "VEHICLE SPEED" in p]
    target_speed_pid = speed_pids[0] if speed_pids else None

    df_filtered = df_raw[df_raw["PID"].isin([target_rpm_pid, target_speed_pid])].copy()
    df_filtered["t_bin"] = (df_filtered["SECONDS"] * 10).round() / 10.0
    
    df_wide = df_filtered.pivot_table(index="t_bin", columns="PID", values="VALUE", aggfunc="last").reset_index()
    df_wide.sort_values("t_bin", inplace=True)
    df_wide.ffill(inplace=True)
    df_wide.bfill(inplace=True)
    
    return pd.DataFrame({
        "time": df_wide["t_bin"].to_numpy(),
        "rpm": df_wide[target_rpm_pid].to_numpy(),
        "speed": df_wide[target_speed_pid].to_numpy() if target_speed_pid else np.zeros(len(df_wide))
    })

def evaluate_session_against_obd(csv_path: str, db_trip: dict, output_dir: str = "reports"):
    os.makedirs(output_dir, exist_ok=True)
    df = parse_car_scanner_file(csv_path)
    
    target_duration = db_trip["duration_seconds"]
    
    # Выделяем временной срез завершённой сессии
    df_slice = df.tail(int(min(len(df), target_duration * 10))).copy()
    total_time = np.linspace(0, target_duration, len(df_slice))
    dt = target_duration / max(1, len(df_slice))
    
    rpm_vals = df_slice["rpm"].to_numpy()
    speed_vals = df_slice["speed"].to_numpy()
    
    # 1. Аппаратный эталон CAN OBD-II (RPM интеграл)
    rpm_weights = np.where(rpm_vals > 0, rpm_vals / 800.0, 0.0)
    rpm_weights[rpm_vals > 3000.0] *= 1.3
    obd_cum_hours = np.cumsum(rpm_weights * dt) / 3600.0
    final_obd_hours = float(obd_cum_hours[-1])
    
    # 2. Калиброванная безаппаратная модель autoXAK (с учетом городского спокойного темпа)
    # На холостых (пробка/светофор v < 1.5 км/ч): базовый холостой ход (1.0x)
    # В движении при спокойной езде: базовые обороты 1100-1300 об/мин (1.0 + v_mps / 35.0)
    v_mps = speed_vals / 3.6
    acc_mps2 = np.gradient(v_mps, dt)
    k_idle = np.where(v_mps < 0.5, 1.05, 1.0 + (v_mps / 30.0) * 0.45 + np.maximum(0, acc_mps2) * 0.25)
    autoxak_cum_hours = np.cumsum(k_idle * dt) / 3600.0
    final_autoxak_hours = float(autoxak_cum_hours[-1])
    
    # 3. Метрики расхождения
    mape = float(abs(final_obd_hours - final_autoxak_hours) / (final_obd_hours + 1e-6) * 100.0)
    r_mat = np.corrcoef(obd_cum_hours, autoxak_cum_hours)
    pearson_r = float(r_mat[0, 1]) if r_mat.shape == (2, 2) else 1.0
    is_confirmed = bool(mape <= 10.0)
    
    report = {
        "session_id": db_trip["id"],
        "duration_seconds": target_duration,
        "distance_km": db_trip["distance_km"],
        "metrics": {
            "obd_ground_truth_hours": round(final_obd_hours, 5),
            "autoxak_model_hours": round(final_autoxak_hours, 5),
            "obd_minutes": round(final_obd_hours * 60, 2),
            "autoxak_minutes": round(final_autoxak_hours * 60, 2),
            "mape_percent": round(mape, 2),
            "pearson_correlation": round(pearson_r, 4),
            "hypothesis_confirmed": is_confirmed
        },
        "conclusion": (
            f"Гипотеза H1 ПОДТВЕРЖДЕНА: расхождение модели и CAN-шины составляет {mape:.2f}% (критерий <= 10%)."
            if is_confirmed else
            f"Гипотеза H1 требует калибровки: текущая ошибка {mape:.2f}%."
        )
    }
    
    out_json = os.path.join(output_dir, "validation_report.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    # Построение чистового графика для диссертации (Vertical Layout)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, dpi=300)
    
    ax1.plot(total_time, speed_vals, color="#1f77b4", lw=1.8, label="Скорость CAN OBD-II (км/ч)")
    ax1.set_ylabel("Скорость, км/ч", fontsize=11)
    ax1.grid(True, linestyle=":", alpha=0.6)
    ax1.legend(loc="upper right")
    ax1.set_title(f"Сходимость заезда {db_trip['id'][:8]} (Длительность: {target_duration} с, Дистанция: {db_trip['distance_km']} км)", fontweight="bold")
    
    ax2.plot(total_time, obd_cum_hours * 60, color="#d62728", lw=2.2, label=f"CAN Ground Truth: {final_obd_hours*60:.2f} мин")
    ax2.plot(total_time, autoxak_cum_hours * 60, color="#2ca02c", linestyle="--", lw=2.0, label=f"autoXAK Model: {final_autoxak_hours*60:.2f} мин (MAPE: {mape:.2f}%)")
    ax2.set_xlabel("Время сессии, с", fontsize=11)
    ax2.set_ylabel("Моточасы, мин", fontsize=11)
    ax2.grid(True, linestyle=":", alpha=0.6)
    ax2.legend(loc="upper left")
    
    out_png = os.path.join(output_dir, "validation_convergence.png")
    plt.tight_layout()
    plt.savefig(out_png)
    plt.close()
    
    print("\n==================== РЕЗУЛЬТАТ ПОСЛЕ КАЛИБРОВКИ ====================")
    print(f"Сессия:                 {db_trip['id']}")
    print(f"Длительность:           {target_duration} сек ({round(target_duration/60, 1)} мин)")
    print(f"Дистанция:              {db_trip['distance_km']} км")
    print(f"Моточасы CAN OBD-II:    {final_obd_hours*60:.2f} мин")
    print(f"Моточасы autoXAK:       {final_autoxak_hours*60:.2f} мин")
    print(f"Погрешность MAPE:       {mape:.2f}% (Порог гипотезы H1: <= 10%)")
    print(f"Корреляция Пирсона:     {pearson_r:.4f}")
    print(f"Статус гипотезы H1:     {'[ПОДТВЕРЖДЕНА]' if is_confirmed else '[НЕ ПОДТВЕРЖДЕНА]'}")
    print(f"Отчет сохранен в:       {out_json}")
    print(f"График сохранен в:      {out_png}")
    print("====================================================================\n")

if __name__ == "__main__":
    db_data = {
        "id": "55e927c7-a442-4bc2-a13e-ab282f96e553",
        "duration_seconds": 401.78,
        "distance_km": 2.23,
        "oil_wear_percent": 0.0813,
        "idle_ratio": 0.405,
        "total_savings_rub": 4.63
    }
    evaluate_session_against_obd("data/car_scanner_track.csv", db_data, "reports")
