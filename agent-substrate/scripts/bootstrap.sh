test "$(stat -fc %T /sys/fs/cgroup)" = cgroup2fs
test -c /dev/kvm
test "$(uname -m)" = x86_64
if command -v docker >/dev/null; then
  echo "Docker already installed; inspect before bootstrap." >&2
  exit 1
fi
cat /etc/apt/sources.list.d/ubuntu.sources
apt-get update -qq
apt-cache policy docker.io curl ca-certificates zstd unzip
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq docker.io curl ca-certificates zstd unzip
systemctl enable --now docker
docker version
mkdir bin
curl --fail --silent --show-error --location --max-time 180 \
  https://github.com/kubernetes-sigs/kind/releases/download/v0.32.0/kind-linux-amd64 -o bin/kind
echo '50030de23cf40a18505f20426f6a8506bedf13c6e509244bd1fa9463721b0f54  bin/kind' | sha256sum -c -
curl --fail --silent --show-error --location --max-time 180 \
  https://github.com/kagent-dev/substrate/releases/download/v0.2.0-beta5/kubectl-ate-linux-amd64 -o bin/kubectl-ate
echo '2cbac58853e7546b079b0b4b949c106efac3810a49208c4bf33360441a490444  bin/kubectl-ate' | sha256sum -c -
curl --fail --silent --show-error --location --max-time 180 \
  https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz -o helm.tar.gz
curl --fail --silent --show-error --location --max-time 60 \
  https://get.helm.sh/helm-v3.19.0-linux-amd64.tar.gz.sha256sum -o helm.sha256sum
sed 's/helm-v3.19.0-linux-amd64.tar.gz/helm.tar.gz/' helm.sha256sum | sha256sum -c -
tar -xzf helm.tar.gz linux-amd64/helm
mv linux-amd64/helm bin/helm
rmdir linux-amd64
chmod 755 bin/kind bin/helm bin/kubectl-ate
cat > kind.yaml <<'YAML'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
  extraMounts:
  - hostPath: /dev/kvm
    containerPath: /dev/kvm
featureGates:
  ClusterTrustBundle: true
  ClusterTrustBundleProjection: true
  PodCertificateRequest: true
runtimeConfig:
  "certificates.k8s.io/v1beta1": "true"
networking:
  ipFamily: ipv4
kubeadmConfigPatches:
- |
  kind: KubeletConfiguration
  serializeImagePulls: false
  maxParallelImagePulls: 4
YAML
export KUBECONFIG=/opt/substrate-mvp/kubeconfig
bin/kind create cluster --name substrate-mvp --config kind.yaml \
  --image kindest/node:v1.37.0@sha256:1aac8018c42eb5d48fae5be507caaee789fc6d9b9b3f560359dd4157ac00d4f6 \
  --wait 180s
docker cp substrate-mvp-control-plane:/usr/bin/kubectl bin/kubectl
chmod 755 bin/kubectl
docker exec substrate-mvp-control-plane sysctl net.ipv4.conf.all.proxy_arp=1
docker exec substrate-mvp-control-plane chmod 666 /dev/kvm
bin/kubectl get nodes -o wide
bin/kubectl api-resources --api-group=certificates.k8s.io
sha256sum bin/*
