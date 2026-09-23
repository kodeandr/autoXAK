import urllib.request, json, ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

req = urllib.request.Request('https://127.0.0.1:8000/api/v1/vehicles/catalog')
with urllib.request.urlopen(req, context=ctx) as r:
    catalog = json.loads(r.read().decode('utf-8'))

print("\n" + "=" * 85)
print("          ИЕРАРХИЧЕСКИЙ РЕЕСТР МОДИФИКАЦИЙ В POSTGRESQL (TOP-FLEET)")
print("=" * 85)
for mk in catalog:
    make_name = mk["name"]
    print(f"\n[МАРКА] {make_name}")
    for md in mk["models"]:
        model_name = md["name"]
        body_type = md["body_type"]
        print(f"   └── Модель: {model_name} ({body_type})")
        for tr in md["trims"]:
            trim_id = tr["id"]
            badge = tr["badge_name"]
            oil_grade = tr["oil_grade"]
            capacity = tr["oil_capacity_l"]
            brand = tr["recommended_oil_brand"]
            print(f"         • [{trim_id}] {badge} | Масло: {oil_grade}, {capacity}л ({brand})")
print("=" * 85 + "\n")
