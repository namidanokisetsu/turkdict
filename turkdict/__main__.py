import argparse
from pathlib import Path
from .common import dumps, write_json
from .pipeline import Pipeline


def main():
    parser = argparse.ArgumentParser(description='Pinned offline Turkish dictionary pipeline')
    parser.add_argument('stage', choices=('fetch', 'normalize', 'frequency', 'analyze', 'rank', 'export', 'validate', 'build'))
    parser.add_argument('--config', default='config/build.json')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    args = parser.parse_args()
    pipeline = Pipeline(args.root, args.root / args.config)
    pipeline.run(args.stage)
    result = dict(outputs={k: str(v) for k,v in pipeline.outputs.items()}, cache_hits=pipeline.hits)
    if args.stage == 'build':
        (args.root / 'build').mkdir(exist_ok=True)
        write_json(args.root / 'build/latest.json', result)
    print(dumps(result))


if __name__ == '__main__':
    main()
