#!/usr/bin/env python3
"""Docker healthcheck for OpenJarvis. Standard library only.

Healthy means "the HTTP server answers". /health returns 503 when the
inference engine reports unhealthy (for example no provider key); that is a
config problem, not a dead server, so it is reported but does not fail the
check. Connection errors, timeouts and any other status fail it.
"""

import os
import sys
import urllib.error
import urllib.request

URL = os.environ.get("OPENJARVIS_HEALTH_URL", "http://127.0.0.1:8000/health")


def main() -> int:
    try:
        with urllib.request.urlopen(URL, timeout=8) as resp:
            print(f"ok {resp.status}")
            return 0
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            print("up, but engine unhealthy (503): check the provider API key")
            return 0
        print(f"unexpected status {exc.code}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"no answer from {URL}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
