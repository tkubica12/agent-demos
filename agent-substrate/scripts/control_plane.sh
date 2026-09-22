export PATH="/opt/substrate-mvp/bin:$PATH"
export KUBECONFIG=/opt/substrate-mvp/kubeconfig
kubectl get --raw /apis/certificates.k8s.io/v1beta1 > certificate-api.json
kubectl create namespace ate-system
mkdir private
kubectl-ate admin make-ca-pool --ca-id=1 --name=egress-mitm-ca-pool \
  --secret-namespace=ate-system --key-type=ECDSAP256
python3 - <<'PY'
import json, secrets
values = {
    "image": {"tag": "v0.2.0-beta5"},
    "rustfs": {"storageSize": "40Gi", "accessKey": secrets.token_hex(16),
               "secretKey": secrets.token_hex(32)},
    "credentialProvider": {"namespacePolicies": [
        {"atespace": "mvp", "allowedNamespaces": ["mvp-secrets"]}]},
}
with open("private/values.json", "x") as f:
    json.dump(values, f)
with open("private/s3.env", "x") as f:
    f.write(f"AWS_ACCESS_KEY_ID={values['rustfs']['accessKey']}\n"
            f"AWS_SECRET_ACCESS_KEY={values['rustfs']['secretKey']}\n"
            "AWS_REGION=us-east-1\n")
PY
helm pull oci://ghcr.io/kagent-dev/substrate/helm/substrate-crds --version 0.2.0-beta5
helm pull oci://ghcr.io/kagent-dev/substrate/helm/substrate --version 0.2.0-beta5
sha256sum substrate*.tgz
helm install substrate-crds ./substrate-crds-0.2.0-beta5.tgz -n ate-system --wait --timeout 120s
helm install substrate ./substrate-0.2.0-beta5.tgz -n ate-system \
  -f private/values.json --timeout 120s > private/helm-install-output.txt
kubectl -n ate-system get pods,pvc,jobs -o wide
kubectl get nodes -o json
kubectl get clustertrustbundles
