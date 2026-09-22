"""Render the bounded WorkerPool and native ActorTemplate without image builds."""

import json
from pathlib import Path

from infra import PRIVATE, ROOT

PYTHON_IMAGE = "docker.io/library/python@sha256:3e2de9c40ca4e3d73240059f9d48baff27908f10293e985a2f382a0378e6df4a"


def template():
    return {
        "metadata": {"atespace": "mvp", "name": "python-microvm"},
        "workerSelector": {"matchLabels": {"workload": "mvp"}},
        "containers": [{
            "name": "workload", "image": PYTHON_IMAGE,
            "command": ["python", "-u", "-c", (ROOT / "scripts" / "workload.py").read_text()],
            "readyz": {"httpGet": {"path": "/readyz", "port": 80}, "timeoutSeconds": 120},
            "env": [{"name": "SSL_CERT_FILE", "value": "/run/ate/trust-bundle.pem"},
                    {"name": "SSL_CERT_DIR", "value": "/run/ate"}],
            "volumeMounts": [{"name": "data", "mountPath": "/data"},
                             {"name": "trust", "mountPath": "/run/ate"}],
        }],
        "resources": {"limits": [{"name": "cpu", "quantity": "1"},
                                {"name": "memory", "quantity": "512Mi"}]},
        "snapshotsConfig": {"onPause": "SNAPSHOT_CONTENT_SCOPE_FULL",
                            "onCommit": "SNAPSHOT_CONTENT_SCOPE_FULL",
                            "storageLocation": "gs://ate-snapshots/mvp/"},
        "sandboxConfig": {"sandboxClass": "SANDBOX_CLASS_MICROVM", "configName": "microvm"},
        "volumes": [{"name": "data", "durableDir": {}},
                    {"name": "trust", "systemInfo": {"dataSources": [
                        {"trustBundle": {"name": "egress-mitm.ate.dev", "path": "trust-bundle.pem"}}]}}],
    }


def prepare():
    payload = json.dumps(template(), indent=2)
    pool = {
        "apiVersion": "ate.dev/v1alpha1", "kind": "WorkerPool",
        "metadata": {"name": "mvp", "namespace": "mvp", "labels": {"workload": "mvp"}},
        "spec": {"replicas": 1, "sandboxClass": "microvm",
                 "workerImage": "ghcr.io/kagent-dev/substrate/ateom-microvm:v0.2.0-beta5",
                 "template": {"nodeSelector": {"kubernetes.io/hostname": "substrate-mvp-control-plane"},
                              "resources": {"requests": {"cpu": "250m", "memory": "1Gi"},
                                            "limits": {"cpu": "1", "memory": "1Gi"}}}},
    }
    script = f"""export PATH="/opt/substrate-mvp/bin:$PATH"
export KUBECONFIG=/opt/substrate-mvp/kubeconfig
kubectl-ate get atespaces -o json
kubectl get clustertrustbundle egress-mitm.ate.dev:mitm:primary-bundle
docker pull {PYTHON_IMAGE}
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  {PYTHON_IMAGE} python -c 'import platform; assert platform.python_version() == "3.13.15"; print(platform.python_version())'
kubectl create namespace mvp
cat > workerpool.json <<'JSON'
{json.dumps(pool, indent=2)}
JSON
cat > actor-template.json <<'JSON'
{payload}
JSON
kubectl create -f workerpool.json
kubectl-ate create atespace mvp -o json
kubectl-ate create actor-template -f actor-template.json -o json
kubectl -n mvp get workerpool,pods -o wide
"""
    path = PRIVATE / "workload-stage.sh"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(script)
    print(path)


if __name__ == "__main__":
    prepare()
