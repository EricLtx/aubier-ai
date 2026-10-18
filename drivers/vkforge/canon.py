"""The one way vkforge writes and hashes an artefact (ruling L-P13): what is hashed is what is on disk."""
import hashlib
import json
from pathlib import Path


def dumps(obj) -> bytes:
    return (json.dumps(obj, sort_keys=True, indent=1, ensure_ascii=False) + "\n").encode("utf-8")


def sha256(obj) -> str:
    return "sha256:" + hashlib.sha256(dumps(obj)).hexdigest()


def sha256_file(path) -> str:
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(obj, path) -> None:
    Path(path).write_bytes(dumps(obj))
