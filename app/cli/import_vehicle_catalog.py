"""Import reviewed taxonomy explicitly; the default plan never writes to the DB."""
import argparse
import asyncio
import json
from pathlib import Path
from app.services.catalog_normalization import CatalogInputError
from app.services.catalog_import import plan_review, load_plan, create_catalog_schema, set_active, catalog_status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('plan').add_argument('review_dir', type=Path)
    load = commands.add_parser('load')
    load.add_argument('review_dir', type=Path)
    load.add_argument('--activate', action='store_true')
    commands.add_parser('activate').add_argument('snapshot_id')
    commands.add_parser('deactivate')
    commands.add_parser('status')
    args = parser.parse_args()
    try:
        if args.command in ('plan', 'load'):
            plan = plan_review(args.review_dir)
            if args.command == 'plan':
                print(json.dumps(plan.summary(), ensure_ascii=False, indent=2))
                return 0
            if not plan.licensed:
                raise CatalogInputError('Source usage terms are unresolved; review only, no database writes.')

        async def run():
            from app.core.database import engine, async_session_factory
            try:
                if args.command == 'load':
                    await create_catalog_schema(engine)
                async with async_session_factory() as db:
                    async with db.begin():
                        if args.command == 'load':
                            return await load_plan(db, plan, activate=args.activate)
                        if args.command in ('activate', 'deactivate'):
                            return await set_active(db, args.snapshot_id if args.command == 'activate' else None)
                        return await catalog_status(db)
            finally:
                await engine.dispose()
        result = asyncio.run(run())
    except (CatalogInputError, OSError, ValueError) as error:
        parser.exit(2, 'Catalog operation failed: ' + str(error) + '\n')
    except Exception as error:
        parser.exit(2, 'Catalog operation failed (' + type(error).__name__ + '); check DB access locally.\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
