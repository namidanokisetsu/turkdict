"""Fetch a private JDK and Zemberek jars; reuse and verify the resulting lock."""
import hashlib
import json
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / 'config/java.lock.json'


def main():
    urls = ['https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.zip']
    urls += [f'https://raw.githubusercontent.com/ahmetaa/maven-repo/master/zemberek-nlp/zemberek-{m}/0.17.1/zemberek-{m}-0.17.1.jar' for m in ('core', 'morphology', 'tokenization', 'lm')]
    urls += ['https://repo.maven.apache.org/maven2/' + p for p in (
        'com/google/guava/guava/25.1-jre/guava-25.1-jre.jar',
        'com/github/ben-manes/caffeine/caffeine/2.6.2/caffeine-2.6.2.jar',
        'com/google/protobuf/protobuf-java/3.9.0/protobuf-java-3.9.0.jar',
        'org/antlr/antlr4-runtime/4.7/antlr4-runtime-4.7.jar')]
    records = json.loads(LOCK.read_text()) if LOCK.exists() else [dict(url=u, path='tools/' + u.rsplit('/', 1)[1]) for u in urls]
    for record in records:
        path = ROOT / record['path']
        path.parent.mkdir(exist_ok=True)
        if not path.exists():
            temporary = path.with_suffix('.part')
            urllib.request.urlretrieve(record['url'], temporary)
            actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
            if record.get('sha256', actual) != actual:
                raise ValueError('Java dependency checksum mismatch')
            temporary.replace(path)
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if record.get('sha256', actual) != actual:
            raise ValueError('Java dependency checksum mismatch')
        record['sha256'] = actual
        if path.suffix == '.zip':
            with zipfile.ZipFile(path) as z:
                for member in z.infolist():
                    dest = (ROOT / 'tools' / member.filename).resolve()
                    if not dest.is_relative_to((ROOT / 'tools').resolve()):
                        raise ValueError('Unsafe archive path')
                z.extractall(ROOT / 'tools')
        print(path.name, flush=True)
    LOCK.write_text(json.dumps(records, indent=2) + '\n')


if __name__ == '__main__':
    main()
