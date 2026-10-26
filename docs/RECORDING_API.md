# Durable recording API v2

All routes require `Authorization: Bearer <access_token>`. No owner can read or
mutate another owner's records. Missing, invalid, expired or unknown-user tokens
return 401. `/api/v2` does not infer an owner from request bodies or the first
database user. New tables are additive: `recording_vehicles`, `trip_recordings`,
`recording_chunks`; legacy rows and savings totals are unchanged.

## Sequence

1. `PUT /api/v2/vehicle` saves the current profile; GET returns `{vehicle: ...}`
   or `{vehicle: null}` before the first save.
2. Generate a stable trip ID locally before collecting points. Store the creation
   request durably so retries use the same snapshot.
3. `POST /api/v2/trips` with `{trip_id, started_at_ms, vehicle, device}`.
4. `PUT /api/v2/trips/{trip_id}/chunks/{sequence}` with `{gps:[], motion:[]}`.
   Sequence starts at 0. Store each immutable chunk locally before sending and
   remove pending delivery only after server acknowledgement. At most 1000 points
   **total** per chunk. Upload order need not match sequence; sequence range is
   0..119999 (enough for one durable chunk per second over 24 hours). The server
   keeps every point, not a 500-point excerpt.
5. On stop, capture `ended_at_ms` immediately and stop sensor collection. Upload
   outstanding chunks, then `POST /api/v2/trips/{trip_id}/complete` with
   `{ended_at_ms, chunk_count, end_reason, control?}`. `end_reason` is `user`,
   `interrupted`, or `duration_limit`. Do not extend the interval while waiting
   for network or manual control entry. Engine-on from start through end,
   including stops, is an explicit assumption, not a detected fact.
6. `GET /api/v2/trips` lists this owner's recordings with `limit` (1..200,
   default 50) and `offset`. GET `/trips/{trip_id}` returns saved metadata and
   estimate. GET `/trips/{trip_id}/export` downloads full raw `gps` and `motion`
   arrays, immutable vehicle/device snapshot, control, model result, point counts,
   and a chunk manifest (sequence, SHA-256, GPS/motion counts).

Trip creation, chunk upload and completion are idempotent for an identical
validated request. Reusing an ID/sequence with different contents is 409.
Completion requires exactly sequences `0..chunk_count-1` and rejects missing or
extra chunks with 409. Repeated completion returns the stored model result and
never adds totals twice. Completed trips cannot accept new chunks; identical old
chunk retries are accepted. Writes serialize on the trip row (a database write
lock also works in SQLite). Profiles are frozen at trip creation. Updating the
current profile does not alter historical recordings.

## Profile

`vehicle_id` (stable per physical car, 1..100 ASCII letters/digits/`_.:-`) is
required. Optional fields: `make`, `model`, `year`, `transmission`, `mass_kg`,
`displacement_l`, `turbo` (boolean or null). `fuel_type` is `gasoline`, `diesel`
or `unknown`. Unknown values stay null/unknown; no default physical mass or
engine is invented. Recording is allowed with incomplete or unsupported
profiles, but the prediction service can return no estimate. The model's
admissible vehicle range is narrower than the recording schema's storage range.

Device metadata has optional `app_version`, `user_agent`, `placement` strings.

## Raw streams and units

GPS points: `timestamp_ms` (positive integer Unix epoch milliseconds),
`latitude`/`longitude` (degrees), nullable `speed_mps` (m/s), nullable `accuracy_m`
(metres), `speed_source`: `gps`, `derived` or `missing`. Null speed requires
source `missing`; a measured zero with source `gps` is a real stop. GPS accuracy
is required for usable model intervals even though missing accuracy can be
stored. Preserve each new GPS callback independently of motion callbacks.

Motion points: `timestamp_ms` (epoch ms), optional `event_timestamp_ms` (original
DOM event timestamp in ms; normally relative to the browser time origin, with
epoch-based values possible in older browsers), nullable raw
`ax`, `ay`, `az` in m/s², nullable `gx`, `gy`, `gz` in degrees/s, required
`acceleration_includes_gravity` boolean, nullable device-reported `interval_ms`.
For DeviceMotion rotationRate use `gx=beta`, `gy=gamma`, `gz=alpha`; they are
angular speeds about device x/y/z, not orientation angles. Do not replace absent
gyroscope readings with zero. Acceleration axes in a point use the same
gravity convention. Keep the original timestamp and values, not repeated timer
copies or values rounded for the display.

All floats must be finite; unknown fields are rejected. Recorded sessions must
be nonnegative and at most 24 hours. A zero-duration interrupted/empty recording
is retained but cannot yield a fuel estimate. Every point must be within the recorded
start/end interval (end boundary inclusive). Chunks can be uploaded while the
end is unknown but no later than start + 24h. Do not turn missing GPS into speed
zero or bridge network/measurement gaps with invented points.

## Model and manual checks

Completion calls the packaged synchronous fuel service in a worker thread and
persists `fuel_estimate`. Inference failure preserves raw data and completes the
trip with `model_unavailable`, null litres and an explicit quality flag. No fuel
savings or oil metrics are synthesized. `savings_status` is always `unavailable`
until a separately validated virtual driving scenario exists. Partial-coverage
litres must not be labeled as the complete-trip consumption.

Optional `control` is `{distance_km, average_l_per_100km, source, same_interval}`.
Source is `trip_computer`, `obd` or `fillup`. Values must refer to the same start/
end interval to serve as an appropriate validation target; `same_interval:false`
is retained but must be excluded from that validation. These measurements are
kept separate from prediction inputs. After completion, use
`PUT /trips/{trip_id}/control` to add or correct the measurement. This does not
rerun the prediction or change the original completion idempotency key.

The API stores GPS coordinates and raw sensor data. Deployment uses the existing
authenticated owner access; serving the application and this API over HTTPS is
required for browser sensors outside localhost. Browser closure can interrupt
collection; restore only points actually persisted and end at the last recorded
time, never fabricate a background continuation.


## Математическое ядро 2.0

GPSPoint дополнительно принимает altitude_m, altitude_accuracy_m, heading_deg и
speed_accuracy_mps; неизвестные значения не вычисляются автоматически. Новые
неизвестные поля исключаются из канонической сериализации для совместимости
с хешами ранее записанных данных.

В API v1 можно передать reference_math с явными данными математического опыта.
Входы и результаты сохраняются в trip_math_evidence вместе с поездкой; они
доступны только владельцу. В API 2.0 неподтверждённые экономия и масло возвращаются
как null, а не нули. Полный контракт: [REFERENCE_MATH.md](REFERENCE_MATH.md).
