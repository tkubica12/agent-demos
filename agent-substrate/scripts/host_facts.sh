#!/usr/bin/env bash
set -eu
cat /etc/ssh/ssh_host_ed25519_key.pub
uname -r
stat -fc %T /sys/fs/cgroup
python3 - <<'PY'
import fcntl
import json
import os

fd = os.open("/dev/kvm", os.O_RDWR)
api = fcntl.ioctl(fd, 0xAE00, 0)
vm = fcntl.ioctl(fd, 0xAE01, 0)
os.close(vm)
os.close(fd)
assert api == 12
print(json.dumps({"kvm_api": api, "kvm_create_vm": "PASS"}))
PY
