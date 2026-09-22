export PATH="/opt/substrate-mvp/bin:$PATH"
export KUBECONFIG=/opt/substrate-mvp/kubeconfig
kubectl-ate admin make-jwt-pool --key-id=1 --name=actor-id-jwt-pool --secret-namespace=ate-system
kubectl-ate admin make-ca-pool --ca-id=1 --name=actor-id-ca-pool --secret-namespace=ate-system
kubectl-ate admin make-ca-pool --ca-id=1 --name=service-dns-ca-pool --secret-namespace=podcertificate-controller-system
kubectl-ate admin make-ca-pool --ca-id=1 --name=pod-identity-ca-pool --secret-namespace=podcertificate-controller-system
python3 - <<'PY'
import base64, json, subprocess
secret = json.loads(subprocess.check_output([
    "kubectl", "-n", "ate-system", "get", "secret", "actor-id-ca-pool", "-o", "json"]))
with open("private/actor-ca.crt", "xb") as f:
    f.write(base64.b64decode(secret["data"]["tls.crt"]))
discovery = json.loads(subprocess.check_output([
    "kubectl", "get", "--raw", "/.well-known/openid-configuration"]))
issuer = discovery["issuer"]
if issuer not in ("https://kubernetes.default.svc", "https://kubernetes.default.svc.cluster.local"):
    raise RuntimeError(f"Unexpected local issuer: {issuer}")
config = {"actorIdentityJWTProvider": "kubernetes", "jwtProviders": [
    {"name": "kubernetes", "issuer": issuer, "audiences": ["api.ate-system.svc"],
     "certificateAuthorityFile": "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
     "discoveryTokenFile": "/var/run/secrets/kubernetes.io/serviceaccount/token"}]}
with open("private/authentication.yaml", "x") as f:
    json.dump(config, f)
print("Verified native issuer:", issuer)
PY
kubectl -n ate-system create secret generic actor-id-ca-certs --from-file=ca.crt=private/actor-ca.crt
kubectl -n ate-system create configmap ate-api-authentication --from-file=authentication.yaml=private/authentication.yaml
kubectl -n podcertificate-controller-system rollout status deployment/podcertificate-controller --timeout=180s
kubectl -n ate-system rollout status deployment/ate-api-server --timeout=240s
kubectl -n ate-system rollout status daemonset/atelet --timeout=180s
kubectl -n ate-system rollout status deployment/atenet-egress --timeout=120s
kubectl -n ate-system rollout status deployment/atenet-router --timeout=120s
kubectl-ate get atespaces -o json
kubectl -n ate-system get pods,pvc -o wide
kubectl get clustertrustbundles
