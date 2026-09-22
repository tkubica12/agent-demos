export PATH="/opt/substrate-mvp/bin:$PATH"
export KUBECONFIG=/opt/substrate-mvp/kubeconfig
mkdir downloads assets
curl --fail --silent --show-error --location --max-time 300 \
  https://github.com/kata-containers/kata-containers/releases/download/4.0.0/kata-static-4.0.0-amd64.tar.zst \
  -o downloads/kata.tar.zst
mkdir downloads/kata
tar --zstd -xf downloads/kata.tar.zst -C downloads/kata
kroot=/opt/substrate-mvp/downloads/kata/opt/kata
cp "$(readlink -f "$kroot/share/kata-containers/vmlinux.container")" assets/vmlinux
cp "$(readlink -f "$kroot/share/kata-containers/kata-containers.img")" assets/rootfs.img
cp "$kroot/share/defaults/kata-containers/configuration-clh.toml" assets/configuration-clh.toml
curl --fail --silent --show-error --location --max-time 180 \
  https://github.com/cloud-hypervisor/cloud-hypervisor/releases/download/v53.0/cloud-hypervisor-static \
  -o assets/cloud-hypervisor
curl --fail --silent --show-error --location --max-time 180 \
  https://gitlab.com/-/project/21523468/uploads/f505704014ae7a816e515f2a05a93d8b/virtiofsd-v1.14.0.zip \
  -o downloads/virtiofsd.zip
echo '2e4fe9571f492b00baa34bc4e708e950039c5da05b830b31a8d179cb6ac8978e  downloads/virtiofsd.zip' | sha256sum -c -
unzip -q downloads/virtiofsd.zip -d downloads/virtiofsd
cp downloads/virtiofsd/target/x86_64-unknown-linux-musl/release/virtiofsd assets/virtiofsd
cat > assets.sha256 <<'SUMS'
448af3d4e59b22c2987f7df94c213ad40fb53a10d437e42b5ee6c4fce7c29ecc  assets/cloud-hypervisor
15b2e72a78cc08a9bd8a6943e89fb69c88cb3cbeb63069efceade835342ac7d4  assets/virtiofsd
6abc48fa83c58e3db314037105d04ec00c1bc80d85eb2dfb2e2854f414573bca  assets/vmlinux
fbbd40de605ab2f99dc82c777dc5b41f9a00dad3a0f699f2247c306ac9c1204d  assets/rootfs.img
1e67eabdfb9d900bf95a00edb52cacc1efe64f23ea095f8b49ee81c7434bce90  assets/configuration-clh.toml
SUMS
sha256sum -c assets.sha256
chmod 755 assets/cloud-hypervisor assets/virtiofsd
assets/cloud-hypervisor --version
assets/virtiofsd --version
kubectl -n ate-system rollout status deployment/rustfs --timeout=60s
kubectl -n ate-system wait --for=condition=Complete job/rustfs-bucket-init --timeout=60s
endpoint="http://$(kubectl -n ate-system get svc rustfs -o jsonpath='{.spec.clusterIP}'):9000"
for file in cloud-hypervisor virtiofsd vmlinux rootfs.img configuration-clh.toml; do
  docker run --rm --network container:substrate-mvp-control-plane \
    --env-file private/s3.env -e AWS_ENDPOINT_URL="$endpoint" \
    -v "/opt/substrate-mvp/assets:/assets:ro" \
    amazon/aws-cli:2.17.0@sha256:643507c10ada7964ca6157b3d799f030b90577643da9955d319a77399ed80d73 \
    s3 cp "/assets/$file" "s3://ate-snapshots/kata-assets/$file" --only-show-errors
done
python3 - <<'PY'
import json, subprocess
keys = {"cloud-hypervisor": "cloud-hypervisor", "virtiofsd": "virtiofsd",
        "vmlinux": "kata-kernel", "rootfs.img": "kata-image",
        "configuration-clh.toml": "kata-config"}
assets = {}
for line in open("assets.sha256"):
    sha, path = line.split()
    name = path.split("/")[-1]
    assets[keys[name]] = {"url": f"gs://ate-snapshots/kata-assets/{name}", "sha256": sha}
obj = {"apiVersion": "ate.dev/v1alpha1", "kind": "SandboxConfig",
       "metadata": {"name": "microvm"}, "spec": {"sandboxClass": "microvm",
       "pauseImage": "registry.k8s.io/pause:3.10.2@sha256:f548e0e8e3dc1896ca956272154dde3314e8cc4fde0a57577ee9fa1c63f5baf4",
       "assets": {"amd64": assets}}}
with open("sandboxconfig.json", "x") as f:
    json.dump(obj, f, indent=2)
subprocess.run(["kubectl", "create", "-f", "sandboxconfig.json"], check=True)
PY
kubectl get sandboxconfig microvm -o json
