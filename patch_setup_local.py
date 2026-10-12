path = "static/setup.html"
with open(path, "r", encoding="utf-8") as f:
    html = f.read()

# 1. Адаптер каталога (превращает плоский ответ в дерево марок)
adapter_js = """<script>
(function() {
  const _origFetch = window.fetch;
  window.fetch = async function(...args) {
    const res = await _origFetch.apply(this, args);
    const url = (typeof args[0] === 'string') ? args[0] : ((args[0] && args[0].url) || '');
    if (url.includes('/api/v1/vehicles/catalog')) {
      try {
        const clone = res.clone();
        const raw = await clone.json();
        const items = Array.isArray(raw) ? raw : (raw.catalog || []);
        if (items.length > 0 && Array.isArray(items[0].models)) return res;

        const makesMap = {};
        for (const item of items) {
          const mkName = item.make || item.name || 'Марка';
          const mkId = (item.make_id || mkName).toLowerCase().replace(/[^a-z0-9]/gi, '_');
          if (!makesMap[mkId]) {
            makesMap[mkId] = { id: mkId, name: mkName, make: mkName, models: {} };
          }
          const mdName = item.model || 'Модель';
          const mdId = (item.model_id || (mkId + '_' + mdName)).toLowerCase().replace(/[^a-z0-9]/gi, '_');
          if (!makesMap[mkId].models[mdId]) {
            makesMap[mkId] = { id: mdId, name: mdName, model: mdName, body_type: item.body_type || '', trims: [] };
          }
          const bName = item.trim || item.badge_name || item.full_name || item.id;
          const hpMatch = (bName + '').match(/(\\d+)\\s*л\\.с\\./);
          const hp = item.power_hp || (hpMatch ? hpMatch[1] : (item.rated_power_kw ? Math.round(item.rated_power_kw * 1.36) : '-'));
          makesMap[mkId].models[mdId].trims.push({
            id: item.id,
            badge_name: bName,
            name: bName,
            trim: bName,
            curb_weight_kg: item.curb_weight_kg || '-',
            power_hp: hp,
            oil_capacity_l: item.oil_capacity_l || '-',
            oil_grade: item.oil_grade || '-',
            oil_spec: item.oil_spec || '-',
            recommended_oil_brand: item.recommended_oil_brand || item.oil_spec || '-'
          });
        }
        const tree = Object.values(makesMap).map(m => ({
          id: m.id,
          name: m.name,
          make: m.name,
          models: Object.values(m.models)
        }));
        return new Response(JSON.stringify(tree), { status: 200, headers: { 'Content-Type': 'application/json' } });
      } catch (err) {
        console.error('Catalog Adapter Error:', err);
      }
    }
    return res;
  };
})();
</script>"""

if "Catalog Adapter Error" not in html:
    html = html.replace("<head>", "<head>\n  " + adapter_js, 1)

# 2. Безопасная функция fetchLiveFuelPrice с гарантированным fallback от NaN
safe_fuel_fn = """async function fetchLiveFuelPrice(grade) {
    const input = document.getElementById("input-fuel-price");
    const DEFAULT_PRICES = { "АИ-92": 65.20, "ai92": 65.20, "АИ-95": 72.40, "ai95": 72.40, "АИ-100": 101.50, "ai100": 101.50, "ДТ": 80.60, "dt": 80.60 };
    const gStr = (grade || "АИ-95").toString().trim();
    let finalPrice = DEFAULT_PRICES[gStr] || 72.40;
    try {
        const rawGrade = gStr.toLowerCase().replace(/[^a-z0-9а-я]/g, "");
        let norm = "ai95";
        if (rawGrade.includes("92")) norm = "ai92";
        else if (rawGrade.includes("100")) norm = "ai100";
        else if (rawGrade.includes("дт") || rawGrade.includes("dt")) norm = "dt";
        const res = await fetch(`/api/v1/fuel/current-price?region_code=77&brand=rosneft&fuel_grade=${norm}`);
        if (res.ok) {
            const data = await res.json();
            const apiVal = parseFloat(data.recommended_price_rub || (data.prices && data.prices[norm]));
            if (!isNaN(apiVal) && apiVal > 0) finalPrice = apiVal;
        }
    } catch (_) {}
    if (input) input.value = Number(finalPrice).toFixed(2);
    return finalPrice;
}"""

# Замена старой fetchLiveFuelPrice по границам скобок
start = html.find("async function fetchLiveFuelPrice")
if start == -1:
    start = html.find("function fetchLiveFuelPrice")

if start != -1:
    b_open = html.find("{", start)
    depth = 1
    pos = b_open + 1
    while depth > 0 and pos < len(html):
        if html[pos] == "{": depth += 1
        elif html[pos] == "}": depth -= 1
        pos += 1
    html = html[:start] + safe_fuel_fn + html[pos:]

with open(path, "w", encoding="utf-8") as f:
    f.write(html)
print("static/setup.html успешно стабилизирован!")
