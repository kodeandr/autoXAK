"""Train physics, direct CatBoost, and residual CatBoost on fixed vehicle splits."""
import json
import os
import platform
from pathlib import Path
import numpy as np
import pandas as pd
from scipy.optimize import lsq_linear
from catboost import CatBoostRegressor
from fuel_model import FEATURES, ROAD, physical_prediction, physics_matrix

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "reports"
MODELS = ROOT / "models"
SEED = 20261018
NAMES = ["physics", "catboost", "hybrid"]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".matplotlib"))


def metrics(frame, truth="fuel_l"):
    output = []
    y = frame[truth].to_numpy()
    for model in NAMES:
        error = frame[model].to_numpy() - y
        output.append({"model": model, "n": len(y), "vehicles": int(frame.vehicle_id.nunique()),
                       "mae_l": float(np.mean(np.abs(error))), "rmse_l": float(np.sqrt(np.mean(error**2))),
                       "bias_l": float(np.mean(error)),
                       "wape_pct": float(100*np.sum(np.abs(error))/np.sum(y)) if np.sum(y) > 0 else None})
    return output


def markdown_table(frame, columns):
    return "| " + " | ".join(columns) + " |\n| " + " | ".join(["---"]*len(columns)) + " |\n" + "\n".join(
        "| " + " | ".join(f"{value:.4f}" if isinstance(value, float) else str(value)
                             for value in row) + " |" for row in frame[columns].itertuples(index=False, name=None))


def main():
    MODELS.mkdir(exist_ok=True)
    data = pd.read_csv(ROOT / "data/processed/windows.csv.gz")
    fragments = pd.read_csv(ROOT / "data/processed/fragments.csv")
    audit = json.loads((REPORT / "audit.json").read_text())
    train = data[data.split == "train"].copy()
    validation = data[data.split == "validation"].copy()
    test = data[data.split == "test"].copy()
    train_ids, val_ids, test_ids = [set(x.vehicle_id) for x in (train, validation, test)]
    assert not (train_ids & val_ids or train_ids & test_ids or val_ids & test_ids)
    assert all(np.isfinite(x[FEATURES].to_numpy()).all() for x in (train, validation, test))
    # Fit TWO global physical coefficients on train only. Effective wheel efficiency 0.10..0.45;
    # engine-on/auxiliary fuel 0.1..3 L/h. No fitting per held-out car or test-label access.
    calibration = lsq_linear(physics_matrix(train), train.fuel_l,
                             bounds=([1/(32*0.45), .1], [1/(32*.10), 3.0]))
    coefficients = calibration.x
    for frame in (train, validation, test):
        frame["physics"] = physical_prediction(frame, coefficients)
    config = dict(iterations=500, depth=6, learning_rate=.06, loss_function="RMSE",
                  random_seed=SEED, thread_count=4, allow_writing_files=False)
    direct = CatBoostRegressor(**config)
    direct.fit(train[FEATURES], train.fuel_l, eval_set=(validation[FEATURES], validation.fuel_l),
               early_stopping_rounds=60, verbose=100)
    direct.save_model(str(MODELS / "catboost_direct.cbm"))
    hybrid_features = FEATURES + ["physics"]
    residual = CatBoostRegressor(**config)
    residual.fit(train[hybrid_features], train.fuel_l - train.physics,
                 eval_set=(validation[hybrid_features], validation.fuel_l - validation.physics),
                 early_stopping_rounds=60, verbose=100)
    residual.save_model(str(MODELS / "catboost_residual.cbm"))
    clipping = {}
    all_metrics, trip_frames = [], []
    for split, frame in (("validation", validation), ("test", test)):
        raw_direct = direct.predict(frame[FEATURES])
        raw_hybrid = frame.physics.to_numpy() + residual.predict(frame[hybrid_features])
        clipping[split] = {"direct_negative": int((raw_direct < 0).sum()),
                           "hybrid_negative": int((raw_hybrid < 0).sum()), "windows": len(frame)}
        frame["catboost"] = np.maximum(raw_direct, 0)
        frame["hybrid"] = np.maximum(raw_hybrid, 0)
        all_metrics.extend([{**m, "split": split, "level": "window", "scope": "all"} for m in metrics(frame)])
        trips = frame.groupby(["vehicle_id", "trip_id", "displacement_l"], as_index=False)[
            ["fuel_l", "duration_s", "distance_m"] + NAMES].sum()
        trips = trips.merge(fragments[["vehicle_id", "trip_id", "coverage"]], on=["vehicle_id", "trip_id"], validate="one_to_one")
        # Score only fragments with >=90% retained coverage and >=60 s. Report excludes gaps.
        trips = trips[(trips.coverage >= .90) & (trips.duration_s >= 60)].copy()
        trips["split"] = split
        trip_frames.append(trips)
        for scope, subset in [("all", trips), ("1.4L", trips[np.isclose(trips.displacement_l, 1.4)]),
                               ("2.0L", trips[np.isclose(trips.displacement_l, 2.0)])]:
            if len(subset):
                all_metrics.extend([{**m, "split": split, "level": "recorded_fragment", "scope": scope}
                                    for m in metrics(subset)])
        frame.to_csv(REPORT / f"predictions_{split}.csv.gz", index=False, compression="gzip")
    scores = pd.DataFrame(all_metrics)
    scores.to_csv(REPORT / "metrics.csv", index=False)
    trip_scores = pd.concat(trip_frames, ignore_index=True)
    trip_scores.to_csv(REPORT / "fragment_predictions.csv", index=False)
    by_vehicle = []
    for (split, vehicle), group in trip_scores.groupby(["split", "vehicle_id"]):
        by_vehicle.extend([{**m, "split": split, "vehicle_id": int(vehicle),
                            "displacement_l": float(group.displacement_l.iloc[0])} for m in metrics(group)])
    per_vehicle = pd.DataFrame(by_vehicle)
    per_vehicle.to_csv(REPORT / "metrics_by_vehicle.csv", index=False)
    pd.DataFrame({"feature": hybrid_features, "importance": residual.feature_importances_}).sort_values(
        "importance", ascending=False).to_csv(REPORT / "hybrid_feature_importance.csv", index=False)
    model_card = {
        "seed": SEED, "python": platform.python_version(), "features": FEATURES, "hybrid_features": hybrid_features,
        "physics_coefficients_l_per_mj_l_per_h": coefficients.tolist(), "road_constants": ROAD,
        "effective_wheel_efficiency": float(1 / (32*coefficients[0])),
        "physics_calibration": "bounded least squares on training vehicles only",
        "split_ids": {name: sorted(ids) for name, ids in (("train", train_ids), ("validation", val_ids), ("test", test_ids))},
        "catboost_config": config, "direct_trees": direct.tree_count_, "residual_trees": residual.tree_count_,
        "clipping_counts": clipping, "input_ranges_train": {c: [float(train[c].min()), float(train[c].max())] for c in FEATURES},
        "scope": "Research prototype on VED speed; not validated on phone GPS or counterfactual driving",
    }
    (MODELS / "model_card.json").write_text(json.dumps(model_card, indent=2), encoding="utf-8")
    make_report(audit, model_card, scores, per_vehicle, trip_scores)
    print(scores.to_string(index=False), flush=True)


def make_report(audit, card, scores, per_vehicle, trip_scores):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    main = scores[(scores.split == "test") & (scores.level == "recorded_fragment") & (scores.scope == "all")]
    relevant = scores[(scores.split == "test") & (scores.level == "recorded_fragment") & (scores.scope != "all")]
    windows = scores[(scores.split == "test") & (scores.level == "window")]
    sources = audit["label_source_seconds"]
    total_s = sum(sources.values())
    source_lines = "\n".join(f"- {name}: {100*seconds/total_s:.2f}% сохранённого времени." for name, seconds in sources.items())
    # Paired cluster bootstrap: whole cars, not independent windows from one car.
    held_trips = trip_scores[trip_scores.split == "test"].copy()
    for name in NAMES:
        held_trips[name + "_ae"] = np.abs(held_trips[name] - held_trips.fuel_l)
    totals = held_trips.groupby("vehicle_id")[["fuel_l"] + [n + "_ae" for n in NAMES]].sum()
    rng = np.random.default_rng(SEED)
    samples = rng.integers(0, len(totals), size=(2000, len(totals)))
    bootstrap = totals.to_numpy()[samples].sum(axis=1)
    improvement = 100*(bootstrap[:, 1] - bootstrap[:, 3])/bootstrap[:, 0]
    ci = np.quantile(improvement, [.025, .975])
    (REPORT / "uncertainty.json").write_text(json.dumps({
        "method": "paired vehicle-cluster bootstrap, 2000 resamples, fixed trained models",
        "hybrid_wape_reduction_vs_physics_95pct_interval_pp": ci.tolist(),
        "vehicles": len(totals), "seed": SEED,
    }, indent=2), encoding="utf-8")
    main_wape = main.set_index("model").wape_pct
    text = f"""# АвтоХАК — первый воспроизводимый эксперимент

Дата: 18 октября 2026. Статус: модели обучены локально; интеграция с приложением и проверка на Audi ещё не выполнены.

**Первый результат:** WAPE физической модели — {main_wape['physics']:.2f}%, гибрида — {main_wape['hybrid']:.2f}%,
прямого CatBoost — {main_wape['catboost']:.2f}%. Гибрид улучшил физическую основу на этом разбиении,
но прямой CatBoost оказался лучше. Сохраняем выбранную гибридную архитектуру как исследовательский вариант
и прямой CatBoost как обязательный конкурент; превосходство гибрида не заявляем.

## Что сделано

Загружены обе части VED (54 недельных CSV) и характеристики автомобилей. Контрольные суммы проверены.
После фильтров осталось **{audit['retained_windows']:,} временных участков**, **{audit['retained_vehicles']} автомобилей**,
**{audit['retained_fragments']:,} записанных фрагментов поездок**, **{audit['retained_hours']:.1f} часов**.
Разбиение по автомобилям: обучение — {audit['split_vehicles']['train']}, настройка — {audit['split_vehicles']['validation']},
независимая проверка — {audit['split_vehicles']['test']}. Автомобили разных частей не пересекаются.
Явные дизели исключены: {audit['excluded_diesel_ids']}. Также исключены гибриды и машины без массы/объёма.
Масса — укрупнённая категория из VED в фунтах, переведённая в кг. Снаряжённая масса и загрузка точно неизвестны.

## Независимая проверка на новых автомобилях

Основная единица оценки — записанный фрагмент длительностью не менее 60 с, для которого сохранилось не менее 90% времени.
Сравниваются суммы предсказаний и разметки **только на одинаковых сохранённых участках**.
В итоговую оценку фрагментов попали 28 из 29 тестовых машин; у одной не оказалось фрагментов,
прошедших критерии длительности и покрытия. В оценке отдельных временных участков участвуют все 29.
Начала/концы поездок в VED удалены при обезличивании: это не полный расход от запуска двигателя до парковки.
WAPE = сумма абсолютных ошибок / сумма контрольного расхода × 100%; это не «процент точности».

{markdown_table(main, ['model', 'vehicles', 'n', 'mae_l', 'wape_pct', 'bias_l'])}

95%-й интервал уменьшения WAPE гибридом относительно физики: **[{ci[0]:.2f}; {ci[1]:.2f}] процентного пункта**
(парный bootstrap по автомобилям, 2000 повторов, модели фиксированы). Если интервал включает ноль,
наблюдаемое улучшение недостаточно устойчиво, чтобы утверждать преимущество для новых машин.
Это не интервал ошибки литров конкретной поездки и не учитывает систематическую ошибку MAF-разметки.

Ошибки временных участков (обычно 30 с):

{markdown_table(windows, ['model', 'n', 'mae_l', 'wape_pct'])}

Группы с объёмами двигателей, близкими к вашему пилоту:

{markdown_table(relevant, ['scope', 'model', 'vehicles', 'n', 'mae_l', 'wape_pct'])}

Малое число тестовых машин в группе ограничивает выводы. Совпадение объёма не означает совпадения марки,
двигателя, коробки и поведения. Нельзя переносить эти числа на Audi Q3 или A4 без отдельной проверки.
Для 1,4 л после фильтрации: четыре обучающие машины, ни одной validation-машины с пригодной разметкой
и одна тестовая. Поэтому даже подбор числа деревьев не проверяет поведение именно этой группы отдельно.

## Что именно обучено

1. `physics`: сопротивление качению, аэродинамика и положительная тяговая энергия на ровной дороге.
Два общих коэффициента настроены только на обучающих автомобилях: {card['physics_coefficients_l_per_mj_l_per_h'][0]:.5f} л/МДж
и {card['physics_coefficients_l_per_mj_l_per_h'][1]:.5f} л/ч при включённом двигателе. Это калиброванная физическая основа.
Второй коэффициент поглощает базовые и неучтённые затраты; его нельзя считать измеренным расходом холостого хода Audi.
2. `catboost`: прямая регрессия литров на участке. Входы включают признаки движения и рассчитанную тяговую энергию.
3. `hybrid`: физическая основа плюс обученная поправка CatBoost. Цель поправки — контрольный расход минус физический.

CatBoost: глубина 6, максимум 500 деревьев, выбор числа деревьев по отдельным validation-автомобилям.
Сохранено деревьев: прямая модель {card['direct_trees']}, поправка {card['residual_trees']}.
Отрицательные оценки итогового расхода ограничиваются нулём; числа таких случаев записаны в model_card.json.
Обороты, MAF, топливные коррекции, Fuel Rate, идентификаторы, координаты, дата и источник разметки **не входят в ML-признаки**.
Тестовые результаты не использованы для настройки параметров или нового разбиения.

## Ограничения контрольного расхода

{source_lines}

Для MAF используется: L/h = MAF[g/s] × (1 + STFT/100 + LTFT/100) / 14.08 / 745 × 3600.
Коррекции двух банков усредняются; отсутствующая коррекция принимается нулевой и явно отмечается в источнике.
14.08 — допущение E10 из статьи VED; 745 г/л — заданная номинальная плотность, не измерение топлива этих машин.
OEM Fuel Rate также является оценкой автомобиля. Обучение на этих целях не доказывает точность относительно заправки.
Отсечка топлива при торможении, обогащение смеси, старт-стоп, прогрев и реальный состав бензина не восстановлены надёжно.

## Обработка времени и входов

Расход интегрируется по исходным временным меткам с удержанием последнего значения сигнала, с переводом в литры.
Скорость интерполируется на сетку 1 с; затем считаются ускорение, остановки, дистанция и энергия.
Участки с разрывами >10 с, отсутствием разметки/скорости, скоростью >200 км/ч или |ускорением| >6 м/с² исключаются.
Обычно участок равен 30 с; остаток >=5 с сохраняется, меньший отбрасывается. Границы поездок не склеиваются.
Двигатель считается работающим всю длительность участка, включая остановки, как согласовано для приложения.

В VED скорость взята из автомобиля, а не из GPS смартфона. Акселерометр и гироскоп телефона в этой выборке отсутствуют.
Эффект шума GPS и качества крепления телефона ещё не проверен. Дорожный уклон, 2ГИС, климат и работа кондиционера
пока не входят в первую модель. Это ограничения версии, а не доказательство ненужности этих признаков.

## Следующий этап

Исправить сохранение исходных событий смартфона и реальных временных меток в приложении; записать контрольные поездки.
Для Audi Q3 2.0 и третьей машины уточнить бензиновую модификацию; для всех машин — год, коробку и массу.
Затем проверить перенос модели на смартфон и настроить параметры на ранних пилотных поездках,
оставив последующие поездки для независимой проверки. Только после этого проверять виртуальный сценарий:
тот же путь и ограничения, иной профиль скорости, одна и та же модель расхода для обеих поездок.
Сейчас модель расхода обучена, но цифровой двойник и доказанная экономия бензина ещё не готовы.

## Источники и воспроизводимость

- [Оригинальный VED](https://github.com/gsoh/VED) и [статья авторов](https://arxiv.org/abs/1905.02081).
- Хеши исходных файлов: `data_manifest.json`, `data/raw/verified_manifest.json`.
- Список машин и разбиение: `reports/vehicles.csv`; покрытие: `reports/vehicle_coverage.csv`.
- Метрики по каждой машине: `reports/metrics_by_vehicle.csv`; предсказания: `reports/predictions_test.csv.gz`.
- Команды повторного запуска и интерфейс: `README.md`.
"""
    (REPORT / "first_experiment.md").write_text(text, encoding="utf-8")
    # Static research plot, independent of app UX. Show all held-out cars, not selected successes.
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), layout="constrained")
    colors = ["#8193a8", "#16a085", "#4267d5"]
    labels = ["Физика", "CatBoost", "Гибрид"]
    axes[0].bar(labels, main.wape_pct, color=colors)
    axes[0].set_ylabel("Ошибка WAPE по фрагментам, %")
    axes[0].set_title("Независимая проверка на VED")
    for i, score in enumerate(main.wape_pct):
        axes[0].text(i, score, f"{score:.1f}%", ha="center", va="bottom")
    held = per_vehicle[per_vehicle.split == "test"]
    for i, name in enumerate(NAMES):
        values = held[held.model == name].wape_pct
        axes[1].scatter(np.full(len(values), i), values, alpha=.55, s=22, color=colors[i])
    axes[1].set_xticks(range(3), labels)
    axes[1].set_ylabel("Ошибка WAPE отдельной машины, %")
    axes[1].set_title("Одна точка — один тестовый автомобиль")
    fig.suptitle("АвтоХАК: прогноз расхода относительно оценки по MAF", fontsize=13)
    fig.savefig(REPORT / "benchmark.png", dpi=160)
    plt.close(fig)


if __name__ == "__main__":
    main()
