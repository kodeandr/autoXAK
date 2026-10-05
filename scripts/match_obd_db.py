import os
import sys
import json
import asyncio
import argparse
import asyncpg
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

async def fetch_trip_from_db(session_id: str = None) -> dict:
    conn = await asyncpg.connect("postgresql://autoXAK_user:autoXAK_pass@postgres:5432/autoXAK_db")
    try:
        if session_id:
            row = await conn.fetchrow("""
                SELECT id::text, user_id, duration_seconds, distance_km, 
                       oil_wear_percent, idle_ratio, total_savings_rub, created_at
                FROM trips WHERE id::text = $1
            """, session_id)
        else:
            row = await conn.fetchrow("""
                SELECT id::text, user_id, duration_seconds, distance_km, 
                       oil_wear_percent, idle_ratio, total_savings_rub, created_at
                FROM trips 
                WHERE duration_seconds > 10.0
                ORDER BY created_at DESC LIMIT 1
            """)
        return dict(row) if row else None
    finally:
        await conn.close()

def evaluate_session(csv_path: str, db_trip: dict, output_dir: str = "reports"):
    os.makedirs(output_dir, exist_ok=True)
    df = parse_car_scanner_file(csv_path)
    
    target_duration = float(db_trip["duration_seconds"])
    total_obd_duration = float(df["time"].max() - df["time"].min())
    
    # Берем временной срез поездки
    n_samples = int(min(len(df), max(200, target_duration * 10)))
    df_slice = df.tail(n_samples).copy()
    
    total_time = np.linspace(0, target_duration, len(df_slice))
    dt = target_duration / max(1, len(df_slice))
    
    rpm_vals = df_slice["rpm"].to_numpy()
    speed_vals = df_slice["speed"].to_numpy()
    
    # 1. Аппаратный эталон CAN OBD-II (интеграл RPM)
    rpm_weights = np.where(rpm_vals > 0, rpm_vals / 800.0, 0.0)
    rpm_weights[rpm_vals > 3000.0] *= 1.3
    obd_cum_hours = np.cumsum(rpm_weights * dt) / 3600.0
    final_obd_hours = float(obd_cum_hours[-1])
    
    # 2. Модель autoXAK (расчет по износу из базы данных)
    # Ресурс: 250 ч. Hours = (oil_wear_percent / 100) * 250
    wear_pct = float(db_trip["oil_wear_percent"])
    final_autoxak_hours = (wear_pct / 100.0) * 250.0
    autoxak_cum_hours = np.linspace(0, final_autoxak_hours, len(df_slice))
    
    # 3. Метрики расхождения
    mape = float(abs(final_obd_hours - final_autoxak_hours) / (final_obd_hours + 1e-6) * 100.0)
    r_mat = np.corrcoef(obd_cum_hours, autoxak_cum_hours)
    pearson_r = float(r_mat[0, 1]) if r_mat.shape == (2, 2) else 1.0
    is_confirmed = bool(mape <= 10.0)
    
    report = {
        "session_id": db_trip["id"],
        "user_id": db_trip.get("user_id"),
        "created_at": str(db_trip.get("created_at")),
        "duration_seconds": target_duration,
        "distance_km": float(db_trip["distance_km"]),
        "oil_wear_percent": wear_pct,
        "total_savings_rub": float(db_trip.get("total_savings_rub", 0.0)),
        "metrics": {
            "obd_ground_truth_hours": round(final_obd_hours, 5),
            "autoxak_model_hours": round(final_autoxak_hours, 5),
            "obd_minutes": round(final_obd_hours * 60, 2),
            "autoxak_minutes": round(final_autoxak_hours * 60, 2),
            "mape_percent": round(mape, 2),
            "pearson_correlation": round(pearson_r, 4),
            "hypothesis_confirmed": is_confirmed
        }
    }
    
    out_json = os.path.join(output_dir, "validation_report.json")
    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        
    # График (Vertical Subplots для диплома)
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, dpi=300)
    
    ax1.plot(total_time, speed_vals, color="#1f77b4", lw=1.8, label="Скорость CAN OBD-II (км/ч)")
    ax1.set_ylabel("Скорость, км/ч", fontsize=11)
    ax1.grid(True, linestyle=":", alpha=0.6)
    ax1.legend(loc="upper right")
    ax1.set_title(f"Сходимость заезда {db_trip['id'][:8]} (Длительность: {target_duration:.1f} с, Дистанция: {db_trip['distance_km']} км)", fontweight="bold")
    
    ax2.plot(total_time, obd_cum_hours * 60, color="#d62728", lw=2.2, label=f"CAN Ground Truth: {final_obd_hours*60:.2f} мин")
    ax2.plot(total_time, autoxak_cum_hours * 60, color="#2ca02c", linestyle="--", lw=2.0, label=f"autoXAK (БД): {final_autoxak_hours*60:.2f} мин (MAPE: {mape:.2f}%)")
    ax2.set_xlabel("Время сессии, с", fontsize=11)
    ax2.set_ylabel("Моточасы, мин", fontsize=11)
    ax2.grid(True, linestyle=":", alpha=0.6)
    ax2.legend(loc="upper left")
    
    out_png = os.path.join(output_dir, "validation_convergence.png")
    plt.tight_layout()
    plt.savefig(out_png)
    plt.close()
    
    print("\n==================== РЕЗУЛЬТАТ ВЕРИФИКАЦИИ ====================")
    print(f"Сессия из БД:           {db_trip['id']}")
    print(f"Пользователь:           {db_trip.get('user_id')}")
    print(f"Время записи:           {db_trip.get('created_at')}")
    print(f"Длительность:           {target_duration:.1f} сек ({target_duration/60:.2f} мин)")
    print(f"Дистанция:              {db_trip['distance_km']} км")
    print(f"Износ масла (БД):       {wear_pct}%")
    print(f"Моточасы CAN OBD-II:    {final_obd_hours*60:.2f} мин")
    print(f"Моточасы autoXAK (БД):  {final_autoxak_hours*60:.2f} мин")
    print(f"Погрешность MAPE:       {mape:.2f}% (Порог гипотезы H1: <= 10%)")
    print(f"Корреляция Пирсона:     {pearson_r:.4f}")
    status_str = "[ПОДТВЕРЖДЕНА]" if is_confirmed else "[НЕ ПОДТВЕРЖДЕНА]"
    print(f"Статус гипотезы H1:     {status_str}")
    print(f"Отчет сохранен в:       {out_json}")
    print(f"График сохранен в:      {out_png}")
    print("===============================================================\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/car_scanner_track.csv")
    parser.add_argument("--session", default=None, help="UUID сессии из БД")
    parser.add_argument("--out", default="reports")
    args = parser.parse_args()
    
    trip = asyncio.run(fetch_trip_from_db(args.session))
    if not trip:
        print("[!] Ошибка: в базе данных не найдены подходящие поездки.")
        sys.exit(1)
        
    evaluate_session(args.csv, trip, args.out)
