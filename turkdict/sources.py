"""Only explicit fetch may access the network; every input is content pinned."""
import shutil
from pathlib import Path
from urllib.request import urlopen

from .common import digest


def verify_environment(root):
    import importlib.metadata
    from .common import read_json
    lock = root / 'requirements.lock'
    if lock.exists():
        for requirement in lock.read_text().splitlines():
            if requirement.strip() and not requirement.startswith('#'):
                name, version = requirement.split('==')
                if importlib.metadata.version(name) != version:
                    raise ValueError(f'Dependency differs from lock: {name}')
    resources = root / 'config/resources.lock.json'
    if resources.exists():
        for resource in read_json(resources):
            dist = importlib.metadata.distribution(resource['package'])
            if dist.version != resource['version'] or digest(dist.locate_file(resource['path'])) != resource['sha256']:
                raise ValueError(f'Package resource differs from lock: {resource["id"]}')


def verify(path, source):
    path = Path(path)
    if path.suffix == ".part" or not path.is_file():
        raise ValueError(f"Missing/incomplete source: {path}; run fetch explicitly")
    if digest(path) != source["sha256"]:
        raise ValueError(f"Checksum mismatch: {path}")
    if source.get("bytes") is not None and path.stat().st_size != source["bytes"]:
        raise ValueError(f"Size mismatch: {path}")
    return path


def fetch(source, root):
    target = root / source["path"]
    if target.exists():
        return verify(target, source)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with urlopen(source["url"], timeout=120) as response, part.open("wb") as out:
            shutil.copyfileobj(response, out)
        if digest(part) != source["sha256"]:
            raise ValueError(f"Downloaded checksum mismatch: {source['id']}")
        if source.get("bytes") is not None and part.stat().st_size != source["bytes"]:
            raise ValueError("Downloaded size mismatch")
        part.replace(target)
    finally:
        part.unlink(missing_ok=True)
    return verify(target, source)
