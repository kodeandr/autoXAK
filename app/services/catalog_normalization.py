"""Deterministic review-only catalogue normalization; no network or DB writes.

Source labels and parameters remain claims. No generation, engine, variant or
physical coefficient is inferred from a family name. Collisions are not resolved
by picking the first row. Original bytes must accompany an exported review set.
"""

import ast
import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass

VERSION = "catalog-normalization-1"
NAMESPACE = uuid.UUID("58c5a2eb-098c-52a8-a59b-763c9b2388b7")
MAX_BYTES = 16 * 1024 * 1024
MAKE_ALIASES = {
    "lada": ("lada", "LADA"),
    "lada (ваз)": ("lada", "LADA"),
    "лада": ("lada", "LADA"),
    "ваз": ("lada", "LADA"),
    "vaz": ("lada", "LADA"),
    "škoda": ("skoda", "Skoda"),
    "skoda": ("skoda", "Skoda"),
    "haval": ("haval", "Haval"),
    "geely": ("geely", "Geely"),
    "chery": ("chery", "Chery"),
    "audi": ("audi", "Audi"),
    "uaz": ("uaz", "УАЗ"),
    "уаз": ("uaz", "УАЗ"),
    "li auto": ("li_auto", "Li Auto"),
    "li auto (lixiang)": ("li_auto", "Li Auto"),
    "lixiang": ("li_auto", "Li Auto"),
}
# This map normalizes spelling only. AWD and selectable 4WD remain different.
DRIVES = {"fwd": "FWD", "rwd": "RWD", "awd": "AWD", "4wd": "4WD"}
TRANSMISSIONS = {x.lower(): x for x in ("MT", "AT", "DCT", "CVT", "AMT")}
PARAMETERS = {
    "curb_weight_kg": ("kg", 0, 100000, False),
    "drag_coefficient_area": ("m2", 0, 100, True),
    "rolling_resistance_coeff": ("1", 0, 1, True),
    "drivetrain_efficiency": ("1", 0, 1, False),
    "engine_displacement_l": ("L", 0, 100, False),
    "rated_power_kw": ("kW", 0, 10000, False),
    "oil_capacity_l": ("L", 0, 100, False),
    "oil_grade": (None, None, None, False),
    "oil_spec": (None, None, None, False),
    "recommended_oil_brand": (None, None, None, False),
    "nominal_service_hours": ("h", 0, 100000, False),
    "base_city_fuel_rate_l100km": ("L/100km", 0, 100, False),
}


class CatalogInputError(ValueError):
    pass


def text(value, *, nullable=False, limit=300):
    if value is None and nullable:
        return None
    if not isinstance(value, str):
        raise CatalogInputError("Expected text, not a coerced number or boolean")
    if any(unicodedata.category(c).startswith("C") for c in value):
        raise CatalogInputError("Control/format characters require review")
    result = " ".join(unicodedata.normalize("NFC", value).split())
    if nullable and not result:
        return None
    if not result or len(result) > limit or "<" in result or ">" in result:
        raise CatalogInputError("Empty, oversized or markup-bearing label")
    return result


def key(value):
    # Search/collision key only, never an entity ID or proof of equivalence.
    value = unicodedata.normalize("NFKC", value).casefold()
    return " ".join(value.translate(str.maketrans("–—−", "---")).split())


def identifier(value, *, limit=150):
    cleaned = text(value, limit=limit)
    if cleaned != value:
        raise CatalogInputError(
            "Opaque source ID needs review; normalization cannot rename it"
        )
    return cleaned


def make_key(value):
    normalized = key(value)
    return MAKE_ALIASES.get(normalized, (None, normalized))[1].casefold()


def _json_pairs(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise CatalogInputError(f"Duplicate JSON key: {name[:80]}")
        result[name] = value
    return result


def _nonfinite(value):
    raise CatalogInputError("Nonfinite JSON constants are forbidden")


def _float(value):
    result = float(value)
    if not math.isfinite(result):
        raise CatalogInputError("JSON number exceeds finite floating-point range")
    return result


def strict_json(raw):
    if len(raw) > MAX_BYTES:
        raise CatalogInputError("Input exceeds 16 MiB")
    try:
        return json.loads(
            raw.decode("utf-8-sig"),
            object_pairs_hook=_json_pairs,
            parse_constant=_nonfinite,
            parse_float=_float,
        )
    except (UnicodeError, ValueError, RecursionError) as error:
        raise CatalogInputError(str(error)) from error


@dataclass(frozen=True)
class Source:
    source_id: str
    format: str
    revision: str
    source_url: str
    expected_sha256: str
    license_status: str
    license_evidence: str

    @classmethod
    def load(cls, raw):
        data = strict_json(raw)
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise CatalogInputError("Manifest keys must match the versioned contract")
        if not all(isinstance(v, str) and v.strip() for v in data.values()):
            raise CatalogInputError("Manifest fields must be nonempty strings")
        if not re.fullmatch(r"[a-z0-9_.-]{1,80}", data["source_id"]):
            raise CatalogInputError("Invalid source_id")
        if not re.fullmatch(r"[a-f0-9]{64}", data["expected_sha256"]):
            raise CatalogInputError("An exact SHA-256 is required")
        if data["format"] not in (
            "carsbase_taxonomy",
            "legacy_seed",
            "legacy_tree",
            "frontend_fallback",
        ):
            raise CatalogInputError("Unsupported source format")
        if data["license_status"] not in ("unresolved", "project_license", "licensed"):
            raise CatalogInputError("Unknown license status")
        return cls(**data)

    def check(self, raw):
        if len(raw) > MAX_BYTES:
            raise CatalogInputError("Input exceeds 16 MiB")
        if hashlib.sha256(raw).hexdigest() != self.expected_sha256:
            raise CatalogInputError("Snapshot SHA-256 differs from the source manifest")


def _stable_id(source, kind, external_id):
    identity = json.dumps([source.source_id, kind, external_id], ensure_ascii=False)
    return str(uuid.uuid5(NAMESPACE, identity))


def _year(value):
    if value is None:
        return None
    if type(value) is not int or not 1800 <= value <= 2200:
        raise CatalogInputError(
            "Year must be an integer in the supported 1800..2200 range"
        )
    return value


def _years(row):
    first, last = _year(row.get("year_from")), _year(row.get("year_to"))
    if first is not None and last is not None and last < first:
        raise CatalogInputError("Reversed source year range")
    return {"from": first, "to": last, "status": "source_claimed", "ongoing": None}


def _legacy_rows(raw):
    # Read the literal only. Never import/execute seed Python or expressions.
    try:
        tree = ast.parse(raw.decode("utf-8-sig"))
        assignments = [
            node
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "TOP_FLEET_DATA"
                for t in node.targets
            )
        ]
        if len(assignments) != 1:
            raise CatalogInputError("Exactly one TOP_FLEET_DATA literal is required")
        # Python dict literals otherwise silently discard a repeated key.
        for node in ast.walk(assignments[0].value):
            if isinstance(node, ast.Dict):
                names = [ast.literal_eval(k) for k in node.keys]
                if len(set(names)) != len(names):
                    raise CatalogInputError("Duplicate key in legacy literal")
        result = ast.literal_eval(assignments[0].value)
        json.dumps(result, allow_nan=False)
        return result
    except (ValueError, TypeError, SyntaxError, RecursionError) as error:
        raise CatalogInputError(
            "Legacy seed must contain a bounded literal list"
        ) from error


def _frontend_rows(raw):
    """Read a small JS literal subset; never evaluate JavaScript."""
    document = raw.decode("utf-8-sig")
    match = re.search(r"\bconst\s+FALLBACK_CATALOG\s*=\s*(\[.*?\]);", document, re.DOTALL)
    if match is None:
        raise CatalogInputError("FALLBACK_CATALOG literal not found")
    literal = match[1]
    lexer = re.compile(
        r"""\s+|'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*"|[A-Za-z_$][A-Za-z0-9_$]*|-?\d+(?:\.\d+)?|[\[\]{},:]"""
    )
    parts, offset = [], 0
    while offset < len(literal):
        token = lexer.match(literal, offset)
        if token is None:
            raise CatalogInputError(
                "Only literal tokens are supported; JS is never executed"
            )
        value = token[0]
        offset = token.end()
        if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", value):
            if not literal[offset:].lstrip().startswith(":"):
                raise CatalogInputError(
                    "Only literal property keys, not JS expressions, are allowed"
                )
            value = repr(value)
        parts.append(value)
    data = _legacy_rows(("TOP_FLEET_DATA = " + "".join(parts)).encode())
    if not isinstance(data, list):
        raise CatalogInputError("Expected a frontend make list")
    tree = []
    for make in data:
        if not isinstance(make, dict) or not isinstance(make.get("models"), list):
            raise CatalogInputError("Invalid frontend make container")
        models = []
        for model in make["models"]:
            if not isinstance(model, dict) or not isinstance(model.get("trims"), list):
                raise CatalogInputError("Invalid frontend model container")
            trims = []
            for variant in model["trims"]:
                if not isinstance(variant, dict):
                    raise CatalogInputError("Invalid frontend variant")
                mapped = dict(variant)
                for old, new in (
                    ("mass_kg", "curb_weight_kg"),
                    ("fuel_rate", "base_city_fuel_rate_l100km"),
                ):
                    if old in mapped:
                        if new in mapped:
                            raise CatalogInputError(
                                "Conflicting frontend parameter aliases"
                            )
                        mapped[new] = mapped.pop(old)
                trims.append(mapped)
            models.append({**model, "trims": trims})
        tree.append(
            {"make": {k: v for k, v in make.items() if k != "models"}, "models": models}
        )
    return tree


class Normalizer:
    def __init__(self, source):
        self.source = source
        self.issues = []
        self.rejected = []
        self.makes = {}
        self.models = []
        self.variants = []
        self.input_counts = Counter()

    def issue(self, code, path, message, *, severity="warning"):
        self.issues.append(
            {"code": code, "path": path, "message": message, "severity": severity}
        )

    def reject(self, kind, path, code, message):
        self.rejected.append(
            {"kind": kind, "path": path, "code": code, "reason": message}
        )
        self.issue(code, path, message, severity="error")

    def drift(self, row, allowed, path):
        if set(row) - allowed:
            self.issue(
                "unmapped_source_fields",
                path,
                "Unmapped fields retained in original snapshot; adapter review required",
            )

    def unique(self, records, kind, contexts=None):
        grouped = defaultdict(list)
        for path, row in records:
            try:
                if not isinstance(row, dict):
                    raise CatalogInputError("Expected an object")
                external_id = identifier(row.get("id"))
                grouped[external_id].append((path, row))
            except CatalogInputError as error:
                self.reject(kind, path, "invalid_identity", str(error))
        accepted = []
        for group in grouped.values():
            serial = {
                json.dumps(
                    [contexts[path] if contexts else None, r],
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                )
                for path, r in group
            }
            if len(serial) > 1:
                for path, _ in group:
                    self.reject(
                        kind,
                        path,
                        "conflicting_duplicate_id",
                        "All conflicting rows quarantined; no first-row winner",
                    )
            else:
                accepted.append(group[0])
                for path, _ in group[1:]:
                    self.issue(
                        "identical_duplicate",
                        path,
                        "Exact duplicate collapsed, with the first source location retained",
                    )
        return accepted

    def normalize(self, raw):
        self.__init__(self.source)
        self.source.check(raw)
        data = (
            _legacy_rows(raw)
            if self.source.format == "legacy_seed"
            else (
                _frontend_rows(raw)
                if self.source.format == "frontend_fallback"
                else strict_json(raw)
            )
        )
        legacy = self.source.format != "carsbase_taxonomy"
        if not isinstance(data, list) or len(data) > 10000:
            raise CatalogInputError("Expected at most 10000 makes")
        self.input_counts["makes"] = len(data)
        self.input_counts["models"] = sum(
            len(r["models"])
            for r in data
            if isinstance(r, dict) and isinstance(r.get("models"), list)
        )
        self.input_counts["variants"] = sum(
            len(m["trims"])
            for r in data
            if isinstance(r, dict) and isinstance(r.get("models"), list)
            for m in r["models"]
            if isinstance(m, dict) and isinstance(m.get("trims"), list)
        )
        if (
            self.input_counts["models"] > 200000
            or self.input_counts["variants"] > 200000
        ):
            raise CatalogInputError("Too many model/variant rows")
        make_rows = []
        for i, row in enumerate(data):
            path = f"$[{i}]"
            if legacy and isinstance(row, dict):
                header = row.get("make")
                row = (
                    {**header, "models": row.get("models")}
                    if isinstance(header, dict)
                    else None
                )
            make_rows.append((path, row))
        model_rows = []
        for path, row in self.unique(make_rows, "make"):
            try:
                self.drift(
                    row,
                    {
                        "id",
                        "name",
                        "country",
                        "models",
                        "cyrillic_name",
                        "numeric_id",
                        "popular",
                        "year_from",
                        "year_to",
                        "updated_at",
                    },
                    path,
                )
                name = text(row.get("name"))
                ext = identifier(row["id"])
                models = row.get("models")
                if not isinstance(models, list) or len(models) > 10000:
                    raise CatalogInputError("Make models must be a bounded list")
                country = text(row.get("country"), nullable=True)
                years = _years(row)
                aliases = [name]
                if row.get("cyrillic_name") is not None:
                    aliases.append(text(row["cyrillic_name"]))
                known = MAKE_ALIASES.get(key(name))
                identity, display = known or (
                    _stable_id(self.source, "make", ext),
                    name,
                )
                if identity not in self.makes:
                    self.makes[identity] = {
                        "id": identity,
                        "name": display,
                        "aliases": [],
                        "source_refs": [],
                        "status": "source_claimed",
                        "markets": None,
                    }
                make = self.makes[identity]
                make["aliases"] = sorted(set(make["aliases"] + aliases))
                make["source_refs"].append(
                    {
                        "external_id": ext,
                        "path": path,
                        "country_claim": country,
                        "years_claim": years,
                    }
                )
                if display != name:
                    self.issue(
                        "make_alias_normalized",
                        path + ".name",
                        f"Explicit alias normalized to {display}; external ID preserved",
                    )
                for j, model in enumerate(models):
                    model_rows.append((f"{path}.models[{j}]", model, identity, ext))
            except (CatalogInputError, TypeError) as error:
                self.reject("make", path, "invalid_make", str(error))
        if len(model_rows) > 200000:
            raise CatalogInputError("Too many model rows")
        parents = {path: (make_id, ext) for path, _, make_id, ext in model_rows}
        trim_rows = []
        for path, row in self.unique(
            [(p, r) for p, r, _, _ in model_rows], "model", parents
        ):
            try:
                self.drift(
                    row,
                    {
                        "id",
                        "mark_id",
                        "name",
                        "cyrillic_name",
                        "year_from",
                        "year_to",
                        "class",
                        "updated_at",
                        "body_type",
                        "trims",
                    },
                    path,
                )
                make_id, parent_ext = parents[path]
                name = text(row.get("name"))
                ext = identifier(row["id"])
                if (
                    self.source.format == "carsbase_taxonomy"
                    and row.get("mark_id") != parent_ext
                ):
                    raise CatalogInputError(
                        "Model parent ID does not match containing make"
                    )
                years = _years(row)
                aliases = [name]
                if row.get("cyrillic_name") is not None:
                    aliases.append(text(row["cyrillic_name"]))
                body = text(row.get("body_type"), nullable=True)
                category = text(row.get("class"), nullable=True)
                trims = row.get("trims")
                if legacy and not isinstance(trims, list):
                    raise CatalogInputError("Legacy model trims must be a list")
                identity = ext if legacy else _stable_id(self.source, "model", ext)
                self.models.append(
                    {
                        "id": identity,
                        "make_id": make_id,
                        "name": name,
                        "aliases": sorted(set(aliases)),
                        "source_ref": {"external_id": ext, "path": path},
                        "years_claim": years,
                        "body_type_claim": body,
                        "segment_claim": category,
                        "generation": None,
                        "market": None,
                        "status": "source_claimed",
                        "technical_parameters": None,
                        "family_label_candidate": name.split(",", 1)[0].strip()
                        if "," in name
                        else name,
                    }
                )
                if "," in name:
                    self.issue(
                        "embedded_generation_label",
                        path + ".name",
                        "Original label retained; comma suffix does not establish a generation or its years",
                    )
                if legacy:
                    for j, trim in enumerate(trims):
                        trim_rows.append((f"{path}.trims[{j}]", trim, identity))
            except CatalogInputError as error:
                self.reject("model", path, "invalid_model", str(error))
        trim_parents = {p: model for p, _, model in trim_rows}
        for path, row in self.unique(
            [(p, r) for p, r, _ in trim_rows], "variant", trim_parents
        ):
            self.variant(path, row, trim_parents[path])
        # Same normalized labels remain separate until identity is reviewed.
        labels = defaultdict(list)
        for model in self.models:
            labels[(model["make_id"], key(model["name"]))].append(model)
        for group in labels.values():
            if len(group) > 1:
                for model in group:
                    self.issue(
                        "normalized_label_collision",
                        model["source_ref"]["path"],
                        "Different external IDs share a search label; records are not merged",
                    )
        self.models.sort(key=lambda x: x["id"])
        self.variants.sort(key=lambda x: x["id"])
        makes = sorted(self.makes.values(), key=lambda x: x["id"])
        for make in makes:
            make["source_refs"].sort(key=lambda x: x["external_id"])
        return {
            "normalization_version": VERSION,
            "source": dict(self.source.__dict__),
            "publication_status": "review_only_not_imported",
            "generation_or_variant_accuracy_verified": False,
            "input_counts": dict(self.input_counts),
            "accepted_counts": {
                "makes": len(makes),
                "models": len(self.models),
                "variants": len(self.variants),
            },
            "makes": makes,
            "models": self.models,
            "variants": self.variants,
            "issues": sorted(self.issues, key=lambda x: (x["path"], x["code"])),
            "rejected": self.rejected,
        }

    def variant(self, path, row, parent):
        try:
            self.drift(
                row,
                {
                    "id",
                    "badge_name",
                    "drivetrain_type",
                    "transmission_type",
                    *PARAMETERS,
                },
                path,
            )
            identity = identifier(row["id"], limit=100)
            badge = text(row.get("badge_name"), limit=120)
            drive_claim = text(row.get("drivetrain_type"), nullable=True)
            transmission_claim = text(row.get("transmission_type"), nullable=True)
            drive = DRIVES.get(key(drive_claim)) if drive_claim is not None else None
            transmission = (
                TRANSMISSIONS.get(key(transmission_claim))
                if transmission_claim is not None
                else None
            )
            if drive_claim is not None and drive is None:
                self.issue(
                    "unrecognized_drive",
                    path,
                    "Unknown drive code retained as a source claim",
                )
            if transmission_claim is not None and transmission is None:
                self.issue(
                    "unrecognized_transmission",
                    path,
                    "Unknown transmission retained as a source claim",
                )
            claims = {}
            for field, (unit, lower, upper, include_zero) in PARAMETERS.items():
                value = row.get(field)
                if value is None:
                    continue
                status = "legacy_unverified"
                try:
                    if lower is None:
                        value = text(value, limit=300)
                    elif (
                        type(value) not in (int, float)
                        or not math.isfinite(value)
                        or not (
                            lower <= value <= upper
                            if include_zero
                            else lower < value <= upper
                        )
                    ):
                        raise CatalogInputError(
                            "Invalid numerical parameter or units/type require review"
                        )
                except CatalogInputError:
                    value, status = None, "rejected"
                    self.issue(
                        "invalid_parameter",
                        path + "." + field,
                        "Parameter excluded; inspect original snapshot",
                        severity="error",
                    )
                claims[field] = {
                    "value": value,
                    "unit": unit,
                    "status": status,
                    "path": path + "." + field,
                }
            self.variants.append(
                {
                    "id": identity,
                    "model_id": parent,
                    "badge_name": badge,
                    "source_ref": {"external_id": identity, "path": path},
                    "identity_status": "needs_year_market_engine_identification",
                    "generation": None,
                    "years": None,
                    "market": None,
                    "engine_code": None,
                    "drivetrain_claim": drive,
                    "drivetrain_raw_claim": drive_claim,
                    "transmission_claim": transmission,
                    "transmission_raw_claim": transmission_claim,
                    "parameter_claims": claims,
                    "reference_parameters": {field: None for field in PARAMETERS},
                }
            )
            self.issue(
                "unverified_legacy_parameters",
                path,
                "All legacy technical values retained as claims; no reference parameter is promoted",
            )
        except (CatalogInputError, KeyError) as error:
            self.reject("variant", path, "invalid_variant", str(error))


def coverage(bundle, requests, reviewed_aliases=None):
    """Candidate family matches only; no fuzzy merge or variant coverage claim."""
    makes = {m["id"]: make_key(m["name"]) for m in bundle["makes"]}
    index = defaultdict(list)
    for model in bundle["models"]:
        index[(makes[model["make_id"]], key(model["family_label_candidate"]))].append(
            model
        )
    aliases = {}
    if reviewed_aliases is not None:
        if (
            not isinstance(reviewed_aliases, dict)
            or set(reviewed_aliases) != {"source_sha256", "relation", "requests"}
            or reviewed_aliases["source_sha256"] != bundle["source"]["expected_sha256"]
            or reviewed_aliases["relation"] != "search_candidates_only"
            or not isinstance(reviewed_aliases["requests"], list)
        ):
            raise CatalogInputError(
                "Candidate mappings must bind to this snapshot and cannot merge identities"
            )
        by_external_id = {m["source_ref"]["external_id"]: m for m in bundle["models"]}
        for alias in reviewed_aliases["requests"]:
            request_key = (
                make_key(text(alias["make"])),
                key(text(alias["model_or_family"])),
            )
            if request_key in aliases or not isinstance(
                alias["model_external_ids"], list
            ):
                raise CatalogInputError("Invalid or repeated candidate mapping")
            selected = []
            seen = set()
            for external_id in alias["model_external_ids"]:
                model = by_external_id.get(identifier(external_id))
                if (
                    model is None
                    or makes[model["make_id"]] != request_key[0]
                    or external_id in seen
                ):
                    raise CatalogInputError(
                        "Candidate ID is missing, duplicated or belongs to another make"
                    )
                selected.append(model)
                seen.add(external_id)
            if not selected or not text(alias["rationale"], limit=1000):
                raise CatalogInputError(
                    "An explicit candidate mapping needs IDs and rationale"
                )
            aliases[request_key] = selected
    rows = []
    for request in requests:
        make, name = text(request["make"]), text(request["model_or_family"])
        request_key = (make_key(make), key(name))
        matches = aliases.get(request_key, index.get(request_key, []))
        rows.append(
            {
                "priority": request["priority"],
                "make": make,
                "model_or_family": name,
                "candidate_count": len(matches),
                "candidate_ids": [x["id"] for x in matches],
                "status": "family_candidates_require_review"
                if matches
                else "no_exact_family_candidate",
                "exact_variant_match_verified": False,
                "matching_rule": "reviewed_source_ids_search_only"
                if request_key in aliases
                else "normalized_family_label_candidate",
            }
        )
    return rows


def compare_reviews(left, right):
    """Report same legacy ID conflicts without resolving or merging them."""
    for bundle in (left, right):
        if (
            not isinstance(bundle, dict)
            or bundle.get("normalization_version") != VERSION
        ):
            raise CatalogInputError(
                "Comparison requires versioned normalized review files"
            )

    def index(rows):
        result = {}
        for row in rows:
            if row["id"] in result:
                raise CatalogInputError("Review file contains duplicate IDs")
            result[row["id"]] = row
        return result

    left_variants, right_variants = index(left["variants"]), index(right["variants"])
    left_models, right_models = index(left["models"]), index(right["models"])
    conflicts = []
    for identity in sorted(set(left_variants) & set(right_variants)):
        a, b = left_variants[identity], right_variants[identity]
        for field in sorted(set(a["parameter_claims"]) & set(b["parameter_claims"])):
            ca, cb = a["parameter_claims"][field], b["parameter_claims"][field]
            if ca["status"] == "rejected" or cb["status"] == "rejected":
                continue
            if (ca["value"], ca["unit"]) != (cb["value"], cb["unit"]):
                conflicts.append(
                    {
                        "car_id": identity,
                        "field": field,
                        "left_claim": ca,
                        "right_claim": cb,
                        "resolution": "unresolved_no_automatic_winner",
                    }
                )
        a_name = left_models[a["model_id"]]["name"]
        b_name = right_models[b["model_id"]]["name"]
        if key(a_name) != key(b_name):
            conflicts.append(
                {
                    "car_id": identity,
                    "field": "model_label",
                    "left_claim": a_name,
                    "right_claim": b_name,
                    "resolution": "identity_requires_review",
                }
            )
    return {
        "left_source": left["source"],
        "right_source": right["source"],
        "shared_legacy_ids": len(set(left_variants) & set(right_variants)),
        "conflicts": conflicts,
        "records_merged": False,
    }
