"""Private synthetic test catalog. Never includes downloaded third-party data."""
import asyncio
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import uuid
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from sqlalchemy import delete, update
from app.cli.normalize_vehicle_catalog import run
from app.core.database import async_session_factory, engine
from app.models.catalog import CatalogImport,CatalogModel,CatalogState
from app.services.catalog_import import plan_review,load_plan,set_active,STATE_ID


async def main():
    try:
        command=sys.argv[1]
        async with async_session_factory() as db,db.begin():
            if command=='prepare':
                with tempfile.TemporaryDirectory(prefix='autoxak-catalog-browser-') as folder:
                    root=Path(folder)
                    raw=json.dumps([{'id':'TOYOTA','name':'Toyota','models':[{'id':'COROLLA','mark_id':'TOYOTA','name':'Corolla'},{'id':'RAV4','mark_id':'TOYOTA','name':'RAV4'}]},
                                    {'id':'HAVAL','name':'Haval','models':[{'id':'JOLION','mark_id':'HAVAL','name':'Jolion'}]}]).encode()
                    (root/'source.json').write_bytes(raw)
                    (root/'manifest.json').write_text(json.dumps({'source_id':'browser_catalog_'+uuid.uuid4().hex,
                        'format':'carsbase_taxonomy','revision':'test-only','source_url':'test:synthetic-browser-fixture',
                        'expected_sha256':hashlib.sha256(raw).hexdigest(),'license_status':'project_license',
                        'license_evidence':'Own synthetic test fixture; no technical specifications.'}))
                    run(root/'source.json',root/'manifest.json',root/'review')
                    plan=plan_review(root/'review');result=await load_plan(db,plan,activate=True)
                    print(json.dumps(result))
            elif command=='activate':
                print(json.dumps(await set_active(db,sys.argv[2])))
            elif command=='deactivate':
                print(json.dumps(await set_active(db,None)))
            elif command=='cleanup':
                imported=await db.get(CatalogImport,sys.argv[2])
                if imported and imported.source['source_id'].startswith('browser_catalog_'):
                    previous=None if sys.argv[3]=='null' else sys.argv[3]
                    await set_active(db,previous)
                    await db.flush()
                    await db.execute(delete(CatalogModel).where(CatalogModel.import_id==imported.id))
                    await db.delete(imported)
                print(json.dumps({'cleaned':bool(imported)}))
    finally:
        await engine.dispose()


if __name__=='__main__':asyncio.run(main())
