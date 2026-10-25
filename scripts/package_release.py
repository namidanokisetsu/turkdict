"""Bundle hash-verified, validated KOReader and Yomitan files for a GitHub release."""
import argparse
import hashlib
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from turkdict.common import digest, read_json
from turkdict.editions import definition_packs, installation_basename, pack_basename


def bundle(destination, members):
    """Write deterministic bundles and verify every member against its input hash."""
    hashes = {}
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED) as out:
        def add(name, stream, stored=False):
            if name in hashes:
                raise ValueError('Duplicate bundle member: ' + name)
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
            h = hashlib.sha256()
            with out.open(info, 'w', force_zip64=True) as target:
                while block := stream.read(1024 * 1024):
                    h.update(block)
                    target.write(block)
            hashes[name] = h.hexdigest()
        import io
        for name, data in members:
            if isinstance(data, Path):
                with data.open('rb') as stream:
                    add(name, stream, data.suffix in ('.zip', '.dz'))
            elif isinstance(data, tuple):
                archive_path, member = data
                with zipfile.ZipFile(archive_path) as archive, archive.open(member) as stream:
                    add(name, stream, name.endswith('.dz'))
            else:
                add(name, io.BytesIO(data))
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:
            raise ValueError('Bundle CRC failure')
        for name, expected in hashes.items():
            h = hashlib.sha256()
            with archive.open(name) as stream:
                while block := stream.read(1024 * 1024):
                    h.update(block)
            if h.hexdigest() != expected:
                raise ValueError('Bundle member differs from input: ' + name)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--yomitan', type=Path, default=ROOT/'build/releases/2026-10-01-complete')
    p.add_argument('--koreader', type=Path, default=ROOT/'build/releases/2026-10-02-koreader-v0.1.1')
    p.add_argument('--destination', type=Path, default=ROOT/'build/releases/2026-10-02-publish')
    p.add_argument('--skip-native-validation', action='store_true', help='Package without repeating native checks when explicitly requested')
    args = p.parse_args()
    dest = args.destination.resolve()
    if dest.exists():
        raise ValueError('Use a fresh destination')
    y = read_json(args.yomitan/'release-report.json')
    k = read_json(args.koreader/'koreader-release-report.json')
    if not args.skip_native_validation:
        if not y['ready_to_import'] or not y['browser_validation']['passed'] or not k['native_validation']['passed']:
            raise ValueError('Both editions require matching native validation')
        if y['browser_validation']['artifact_hashes'] != {name: info['sha256'] for name, info in y['files'].items()}:
            raise ValueError('Yomitan native evidence does not match the dictionary ZIPs')
    if k['source_artifact_hashes'] != {name: info['sha256'] for name, info in y['files'].items()}:
        raise ValueError('Editions have different source content')
    for directory, report in ((args.yomitan, y), (args.koreader, k)):
        for name, info in report['files'].items():
            if digest(directory/name) != info['sha256']:
                raise ValueError('Artifact changed after validation: ' + name)
    dest.mkdir(parents=True)
    definitions = definition_packs(y)
    if definition_packs(k) != definitions:
        raise ValueError('Editions have different definition packs')
    expected_yomitan = {pack_basename(pack) + '.zip' for pack in definitions} | {'turkish-forms.zip', 'turkish-etymology.zip'}
    if not expected_yomitan <= set(y['files']) or set(y['files']) - expected_yomitan - {'turkish-frequency.zip'}:
        raise ValueError('Unexpected Yomitan installation files')
    installation_names = {pack_basename(pack): installation_basename(pack) for pack in definitions}
    installation_names.update({f'turkish-{kind}': installation_basename(dict(kind=kind, language='en'))
                               for kind in ('forms', 'frequency', 'etymology', 'morphology')})
    yomitan = [(installation_names[Path(name).stem] + '.zip', args.yomitan/name) for name in y['files']]
    koreader = []
    basenames = {pack_basename(pack) for pack in definitions} | {'turkish-etymology', 'turkish-morphology'}
    for name in k['files']:
        with zipfile.ZipFile(args.koreader/name) as archive:
            basename = name.removeprefix('koreader-').removesuffix('.zip')
            expected = {f'{basename}/{basename}{suffix}'
                        for suffix in ('.ifo', '.idx', '.dict.dz', '.css')}
            cache = f'{basename}/{basename}.idx.oft'
            found = set(archive.namelist())
            if (basename not in basenames
                    or not expected <= found or found - expected - {cache}):
                raise ValueError('Unexpected KOReader installation files: ' + name)
            renamed = installation_names[basename]
            for suffix in ('.css', '.dict.dz', '.idx', '.ifo'):
                koreader.append((f'{renamed}/{renamed}{suffix}', (args.koreader/name, f'{basename}/{basename}{suffix}')))
    for filename, members in [('turkdict-yomitan.zip', yomitan), ('turkdict-koreader.zip', koreader)]:
        bundle(dest/filename, members)
        print(f'{filename}: {(dest/filename).stat().st_size:,} bytes; SHA-256 {digest(dest/filename)}', flush=True)


if __name__ == '__main__':
    main()
