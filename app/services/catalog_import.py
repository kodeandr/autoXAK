"""Explicit plan/load/activate/rollback for taxonomy; legacy vehicle tables are untouched."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re

from sqlalchemy import insert, select, text, func
from app.models.catalog import CatalogImport, CatalogModel, CatalogState
from app.services.catalog_normalization import CatalogInputError, Normalizer, Source, strict_json, MAX_BYTES

STATE_ID = 'taxonomy'
IMPORT_LOCK = 173512342
SCHEMA_LOCK = 173512341
PREFIX = 'catalog_model_'


def vehicle_id(normalized_id):
    # Source namespace is already included by the normalizer's UUID5.
    return PREFIX + normalized_id


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()


def read_review_file(directory, name):
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', name):
        raise CatalogInputError('Invalid review file name')
    path = directory / name
    if path.is_symlink() or not path.is_file():
        raise CatalogInputError('Review file missing or symlinked: ' + name)
    with path.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise CatalogInputError('Review file exceeds 16 MiB: ' + name)
    return raw


@dataclass(frozen=True)
class ImportPlan:
    snapshot_id: str
    bundle: dict

    @property
    def licensed(self):
        return self.bundle['source']['license_status'] in ('licensed', 'project_license')

    def summary(self):
        return {'snapshot_id': self.snapshot_id, 'makes': len(self.bundle['makes']),
                'models': len(self.bundle['models']), 'modifications': 0,
                'can_load': self.licensed, 'license_status': self.bundle['source']['license_status'],
                'legacy_tables_modified': False,
                'selection_requires_declared_fuel_rate': True}


def plan_review(directory):
    """No DB connection/DDL/DML. Reproduce normalized content from original bytes."""
    directory = Path(directory)
    complete = strict_json(read_review_file(directory, 'COMPLETE.json'))
    if not isinstance(complete, dict) or complete.get('status') != 'complete':
        raise CatalogInputError('A complete review package is required')
    expected = complete.get('files_sha256')
    if not isinstance(expected, dict) or not {'source.snapshot', 'source.manifest.json', 'normalized.json'} <= expected.keys():
        raise CatalogInputError('Review checksum manifest is incomplete')
    files = {}
    for name, checksum in expected.items():
        if not isinstance(checksum, str) or not re.fullmatch('[a-f0-9]{64}', checksum):
            raise CatalogInputError('Invalid review checksum')
        files[name] = read_review_file(directory, name)
        if hashlib.sha256(files[name]).hexdigest() != checksum:
            raise CatalogInputError('Review checksum mismatch: ' + name)
    source = Source.load(files['source.manifest.json'])
    if source.format != 'carsbase_taxonomy':
        raise CatalogInputError('Only model taxonomy is imported; legacy technical claims are review-only')
    bundle = Normalizer(source).normalize(files['source.snapshot'])
    if strict_json(files['normalized.json']) != bundle:
        raise CatalogInputError('Normalized JSON is not reproducible from the source')
    if complete.get('source_sha256') != source.expected_sha256:
        raise CatalogInputError('Completion/source checksum mismatch')
    if bundle['rejected'] or any(i['severity'] == 'error' for i in bundle['issues']):
        raise CatalogInputError('Rejected records must be resolved before import')
    if bundle['variants'] or not bundle['models']:
        raise CatalogInputError('A nonempty taxonomy without inferred modifications is required')
    makes = {m['id']: m for m in bundle['makes']}
    for model in bundle['models']:
        if model['make_id'] not in makes:
            raise CatalogInputError('Orphan model')
        if len(vehicle_id(model['id'])) > 100 or len(model['make_id']) > 100:
            raise CatalogInputError('Catalogue ID exceeds database limit')
        # Header/profile names have a 200-character contract; no silent truncation.
        if len(makes[model['make_id']]['name'] + ' ' + model['name']) > 200:
            raise CatalogInputError('Vehicle display name exceeds the existing profile limit')
    return ImportPlan(hashlib.sha256(canonical(bundle)).hexdigest(), bundle)


async def create_catalog_schema(engine):
    """Only new tables. Never run a seed or ALTER legacy vehicles/users/trips."""
    tables = [CatalogImport.__table__, CatalogModel.__table__, CatalogState.__table__]
    async with engine.begin() as connection:
        if connection.dialect.name != 'postgresql':
            raise CatalogInputError('PostgreSQL is required')
        await connection.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': SCHEMA_LOCK})
        await connection.run_sync(lambda sync: CatalogImport.metadata.create_all(sync, tables=tables))


async def lock_import(db):
    if db.bind.dialect.name != 'postgresql':
        raise CatalogInputError('PostgreSQL is required')
    await db.execute(text('SELECT pg_advisory_xact_lock(:key)'), {'key': IMPORT_LOCK})


async def _activate(db, snapshot_id):
    if snapshot_id is not None:
        imported = await db.get(CatalogImport, snapshot_id)
        if imported is None:
            raise CatalogInputError('Unknown snapshot')
        if imported.source.get('license_status') not in ('licensed', 'project_license'):
            raise CatalogInputError('Source terms are unresolved')
        actual = await db.scalar(select(func.count()).select_from(CatalogModel).where(CatalogModel.import_id == snapshot_id))
        if actual != imported.model_count:
            raise CatalogInputError('Snapshot model count is incomplete')
    state = await db.get(CatalogState, STATE_ID)
    previous = state.active_import_id if state else None
    if state is None:
        state = CatalogState(id=STATE_ID)
        db.add(state)
    state.active_import_id = snapshot_id
    return previous


async def load_plan(db, plan, *, activate=False):
    """One transaction, no internal commit; caller commits only after all checks."""
    if not plan.licensed:
        raise CatalogInputError('Source terms are unresolved; load/activation is blocked. Do not relabel the manifest without rights evidence.')
    await lock_import(db)
    imported = await db.get(CatalogImport, plan.snapshot_id)
    if imported is None:
        bundle = plan.bundle
        imported = CatalogImport(id=plan.snapshot_id, source=bundle['source'], makes=bundle['makes'], model_count=len(bundle['models']))
        db.add(imported)
        await db.flush()
        makes = {m['id']: m for m in bundle['makes']}
        rows = [dict(import_id=plan.snapshot_id, id=vehicle_id(m['id']), make_id=m['make_id'],
                     make_name=makes[m['make_id']]['name'], name=m['name'], details=m) for m in bundle['models']]
        for start in range(0, len(rows), 500):
            await db.execute(insert(CatalogModel), rows[start:start+500])
    actual = await db.scalar(select(func.count()).select_from(CatalogModel).where(CatalogModel.import_id == plan.snapshot_id))
    if actual != len(plan.bundle['models']):
        raise CatalogInputError('Stored snapshot count mismatch')
    previous = await _activate(db, plan.snapshot_id) if activate else None
    return {**plan.summary(), 'loaded': True, 'activated': activate, 'previous_active_snapshot': previous}


async def set_active(db, snapshot_id):
    await lock_import(db)
    previous = await _activate(db, snapshot_id)
    return {'active_snapshot': snapshot_id, 'previous_active_snapshot': previous, 'legacy_tables_modified': False}


async def catalog_status(db):
    state = await db.get(CatalogState, STATE_ID)
    return {'active_snapshot': state.active_import_id if state else None,
            'imports': [dict(snapshot_id=i.id, source_id=i.source['source_id'], models=i.model_count,
                             license_status=i.source['license_status'])
                        for i in (await db.scalars(select(CatalogImport).order_by(CatalogImport.created_at.desc()))).all()]}


async def lookup_model(db, car_id, *, snapshot_id=None, active_only=False):
    if not car_id or not car_id.startswith(PREFIX):
        return None
    stmt = select(CatalogModel).join(CatalogImport, CatalogImport.id == CatalogModel.import_id).where(CatalogModel.id == car_id)
    if active_only or snapshot_id is not None:
        if snapshot_id is None:
            state = await db.get(CatalogState, STATE_ID)
            snapshot_id = state.active_import_id if state else None
        if snapshot_id is None:
            return None
        stmt = stmt.where(CatalogModel.import_id == snapshot_id)
    return await db.scalar(stmt.order_by(CatalogImport.created_at.desc(), CatalogImport.id).limit(1))
