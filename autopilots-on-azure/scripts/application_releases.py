from __future__ import annotations

import hashlib
import io
import json
import shlex
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.tf_helpers import REPO_ROOT, run


PYTHON_BASE_IMAGE = (
    "docker.io/library/python@sha256:"
    "aaf08c5490d52cd92bc27142a5215b1e47355a5eaf9adfad2a0fbc022d0b5490"
)
APPROVED_INDEX = "https://packagefeedproxy.microsoft.io/pypi/simple/"
SERVICE_PACKAGES = {
    "gateway": ("", "bridge"),
    "private-mcp": ("private-incidents-mcp", "private_incidents_mcp"),
    "public-mcp": ("public-shipments-mcp", "public_shipments_mcp"),
}


@dataclass(frozen=True)
class ApplicationRelease:
    role: str
    digest: str
    archive: bytes

    @property
    def directory(self) -> str:
        return f"/app/releases/{self.digest}"


def build_release(
    role: str, *, root: Path = REPO_ROOT, base_image: str = PYTHON_BASE_IMAGE,
) -> ApplicationRelease:
    if role not in SERVICE_PACKAGES and role != "runtime":
        raise ValueError(f"No thin application package exists for {role!r}.")
    files: dict[str, bytes] = {}
    if role == "runtime":
        runtime = root / "runtimes" / "hermes"
        files.update({path.name: path.read_bytes() for path in sorted(runtime.glob("*.py"))})
        files["dependency-lock.toml"] = (runtime / "uv.lock").read_bytes()
        packages = [
            (root / "bridge", "bridge"),
            (root / "autopilots_identity", "autopilots_identity"),
            (runtime / "plugins", "runtime_plugins"),
            (runtime / "runtime_skills", "runtime_skills"),
        ]
    else:
        project, package = SERVICE_PACKAGES[role]
        directory = root / project
        exported = run(
            ["uv", "export", "--frozen", "--no-dev", "--no-emit-project", "--format", "requirements-txt"],
            cwd=directory, capture=True,
        ).stdout
        files["requirements.txt"] = exported.encode("utf-8")
        packages = [(directory / package, package)]
    for package_directory, prefix in packages:
        for path in sorted(package_directory.rglob("*")):
            if (
                path.is_file() and "__pycache__" not in path.parts
                and path.suffix not in {".pyc", ".pyo"} and path.name != "Dockerfile"
            ):
                if path.is_symlink():
                    raise ValueError(f"Application releases cannot contain symlinks: {path}")
                name = prefix + "/" + path.relative_to(package_directory).as_posix()
                files[name] = path.read_bytes()
    if not any(name.endswith(".py") for name in files):
        raise ValueError(f"The {role} release contains no Python application.")
    manifest = {
        "schema": 1, "role": role, "base_image": base_image,
        "files": {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    files["release.json"] = manifest_bytes
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in sorted(files.items()):
            member = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100600 << 16
            archive.writestr(member, content)
    return ApplicationRelease(role, digest, output.getvalue())


INSTALL_RELEASE = r"""
import hashlib,json,pathlib,subprocess,sys,zipfile
archive=pathlib.Path(sys.argv[1])
target=pathlib.Path(sys.argv[2])
expected=sys.argv[3]
role=sys.argv[4]
ready=target/'.ready'
reused=ready.exists()
if reused:
    if ready.read_text().strip()!=expected:
        raise RuntimeError('Existing application release has a different identity')
with zipfile.ZipFile(archive) as source:
    names=source.namelist()
    if len(names)!=len(set(names)):
        raise RuntimeError('Application archive contains duplicate members')
    manifest_bytes=source.read('release.json')
    if hashlib.sha256(manifest_bytes).hexdigest()!=expected:
        raise RuntimeError('Application release manifest checksum differs')
    manifest=json.loads(manifest_bytes)
    if manifest.get('schema')!=1 or manifest.get('role')!=role or set(names)!=set(manifest['files'])|{'release.json'}:
        raise RuntimeError('Application release inventory differs')
    for name in names:
        relative=pathlib.PurePosixPath(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name:
            raise RuntimeError('Application release contains an unsafe path')
        content=source.read(name)
        if name!='release.json' and hashlib.sha256(content).hexdigest()!=manifest['files'][name]:
            raise RuntimeError('Application source checksum differs: '+name)
        destination=target.joinpath(*relative.parts)
        if reused:
            if not destination.is_file() or destination.read_bytes()!=content:
                raise RuntimeError('Installed application source changed: '+name)
        else:
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(content)
if reused:
    print(json.dumps({'release':expected,'reused':True}))
    raise SystemExit(0)
venv=target/'.venv'
if role=='runtime':
    if pathlib.Path('/app/uv.lock').read_bytes()!=(target/'dependency-lock.toml').read_bytes():
        raise RuntimeError('Runtime dependency lock changed; build and select its dependency image first')
    if not venv.exists():
        venv.symlink_to('/app/.venv',target_is_directory=True)
    bundled=pathlib.Path('/app/runtime_skills')
    if bundled.exists():
        for source in bundled.iterdir():
            destination=target/'runtime_skills'/source.name
            if not destination.exists():
                destination.parent.mkdir(parents=True,exist_ok=True)
                destination.symlink_to(source,target_is_directory=source.is_dir())
    ready.write_text(expected+'\n')
    print(json.dumps({'release':expected,'reused':False}))
    raise SystemExit(0)
requirements=(target/'requirements.txt').read_bytes()
dependency_id=hashlib.sha256(manifest['base_image'].encode()+b'\0'+requirements).hexdigest()
dependency=pathlib.Path('/app/dependencies')/dependency_id
dependency.mkdir(parents=True,exist_ok=True)
dependency_ready=dependency/'.ready'
dependency_python=dependency/'.venv/bin/python'
if not (target/'installation.log').exists():
    (target/'installation.log').symlink_to(dependency/'installation.log')
if dependency_ready.exists():
    if dependency_ready.read_text().strip()!=dependency_id or not dependency_python.is_file():
        raise RuntimeError('The prepared dependency environment has a different identity')
else:
    subprocess.run([sys.executable,'-m','venv',str(dependency/'.venv')],check=True)
    wheels=dependency/'.wheels'
    wheels.mkdir(exist_ok=True)
    with (dependency/'installation.log').open('a') as log:
        subprocess.run([str(dependency_python),'-m','pip','download','--disable-pip-version-check','--require-hashes',
                        '--only-binary=:all:','--index-url','https://packagefeedproxy.microsoft.io/pypi/simple/',
                        '--dest',str(wheels),'-r',str(target/'requirements.txt')],
                       check=True,stdout=log,stderr=subprocess.STDOUT,timeout=900)
        subprocess.run([str(dependency_python),'-m','pip','install','--disable-pip-version-check','--no-index',
                        '--require-hashes','--find-links',str(wheels),'-r',str(target/'requirements.txt')],
                       check=True,stdout=log,stderr=subprocess.STDOUT,timeout=600)
    dependency_ready.write_text(dependency_id+'\n')
if not venv.exists():
    venv.symlink_to(dependency/'.venv',target_is_directory=True)
ready.write_text(expected+'\n')
print(json.dumps({'release':expected,'reused':False}))
""".strip()


def install_release(sandbox: Any, release: ApplicationRelease) -> str:
    sandbox.ensure_running(timeout=600)
    archive = f"/app/releases/{release.digest}.zip"
    sandbox.write_file(archive, release.archive, mode="0600")
    command = "python3 -c " + shlex.quote(INSTALL_RELEASE)
    command += " " + " ".join(shlex.quote(value) for value in (
        archive, release.directory, release.digest, release.role,
    ))
    result = sandbox.exec(command)
    if result.exit_code != 0:
        raise RuntimeError(
            f"{release.role} release installation failed: {result.stderr}. "
            f"Inspect {release.directory}/installation.log; the previous application was not changed."
        )
    payload = json.loads(result.stdout)
    if payload.get("release") != release.digest:
        raise RuntimeError("The installed application did not attest its expected release manifest.")
    return release.directory
