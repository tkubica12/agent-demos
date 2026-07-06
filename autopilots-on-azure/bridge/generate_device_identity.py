from __future__ import annotations

import json

from bridge.gateway_client import generate_bridge_device_identity


def main() -> None:
    print(json.dumps(generate_bridge_device_identity(), indent=2))


if __name__ == "__main__":
    main()
