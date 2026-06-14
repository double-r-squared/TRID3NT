"""Adversarial egress attack harness (job-0232 LIVE-VERIFY lens).

Drives infra/python-sandbox/executor.py via the local-subprocess fallback
(grace2_agent.sandbox_runner.run_sandbox_local) and fires a battery of egress
escape vectors at the in-process network guard. For EACH vector we record:
  - the result envelope status + the `result` value,
  - whether the in-process guard BLOCKED it (SandboxNetworkBlocked),
  - whether it REACHED a non-allowlisted host (guard FAILED — a real refutation),
  - or whether it escaped the in-process guard but would be stopped by the VPC
    layer ONLY (guard does not cover this vector; honest documentation required).

A vector that REACHES the public internet is a refutation. A vector that escapes
the in-process guard but is documented as VPC-only is NOT a refutation IF the
runner documented it honestly.
"""

from __future__ import annotations

import json
import os
import sys

# Ensure we import the agent's sandbox_runner.
REPO = "/home/nate/Documents/GRACE-2"
sys.path.insert(0, os.path.join(REPO, "services", "agent", "src"))

os.environ["GRACE2_SANDBOX_LOCAL"] = "1"
os.environ["MPLBACKEND"] = "Agg"

from grace2_agent.sandbox_runner import run_sandbox_local  # noqa: E402

# A non-allowlisted host we attempt to reach. We use a routable public host so a
# guard *bypass* would actually establish a TCP connection (proving a true leak),
# vs. example.com which resolves but the connect may hang. We test BOTH a DNS
# name and a raw IP (1.1.1.1 — Cloudflare, responds on 80/443).
TARGET_HOST = "example.com"
TARGET_IP = "1.1.1.1"

VECTORS: dict[str, str] = {
    # 1. urllib high-level
    "urllib_urlopen": (
        "import urllib.request\n"
        "try:\n"
        "    r = urllib.request.urlopen('http://example.com', timeout=6)\n"
        "    result = f'REACHED:{r.status}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 2. raw socket.create_connection (the helper urllib3 uses)
    "socket_create_connection_host": (
        "import socket\n"
        "try:\n"
        "    s = socket.create_connection(('example.com', 80), timeout=6)\n"
        "    result = 'REACHED'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 3. raw socket() + .connect to a raw IP (bypasses DNS entirely)
    "socket_connect_raw_ip": (
        "import socket\n"
        "try:\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(6)\n"
        "    s.connect(('1.1.1.1', 80))\n"
        "    result = 'REACHED'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 4. socket.connect_ex (returns errno, does not raise — guard must also patch)
    "socket_connect_ex_raw_ip": (
        "import socket\n"
        "try:\n"
        "    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "    s.settimeout(6)\n"
        "    rc = s.connect_ex(('1.1.1.1', 80))\n"
        "    result = f'connect_ex_rc={rc}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 5. DNS resolution via getaddrinfo (NOT a connect — the guard patches connect,
    #    not resolution; getaddrinfo itself talks to the resolver/DNS server).
    "dns_getaddrinfo": (
        "import socket\n"
        "try:\n"
        "    info = socket.getaddrinfo('example.com', 80)\n"
        "    result = f'RESOLVED:{info[0][4]}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 6. subprocess curl (shells out — guard patches IN-PROCESS socket only, a child
    #    process has its OWN unpatched socket stack).
    "subprocess_curl": (
        "import subprocess\n"
        "try:\n"
        "    out = subprocess.run(['curl','-sS','--max-time','6','http://example.com'],\n"
        "        capture_output=True, text=True, timeout=10)\n"
        "    result = f'rc={out.returncode};len={len(out.stdout)};err={out.stderr[:80]}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 7. os.system curl (same child-process bypass, different API)
    "os_system_curl": (
        "import os\n"
        "rc = os.system('curl -sS --max-time 6 -o /tmp/grace2_egress_probe http://example.com 2>/tmp/grace2_egress_err')\n"
        "try:\n"
        "    body = open('/tmp/grace2_egress_probe').read()\n"
        "except Exception:\n"
        "    body = ''\n"
        "result = f'rc={rc};bodylen={len(body)}'\n"
    ),
    # 8. ctypes direct connect() syscall — bypasses the Python socket module
    #    entirely by calling libc connect() on a raw fd. The in-process guard
    #    patches socket.socket.connect; it CANNOT patch a libc syscall.
    "ctypes_libc_connect": (
        "import ctypes, struct, socket as _s\n"
        "try:\n"
        "    libc = ctypes.CDLL('libc.so.6', use_errno=True)\n"
        "    fd = libc.socket(_s.AF_INET, _s.SOCK_STREAM, 0)\n"
        "    # sockaddr_in: family(2) port(2,BE) addr(4) pad(8)\n"
        "    sa = struct.pack('!H', _s.AF_INET) + struct.pack('!H', 80) + _s.inet_aton('1.1.1.1') + b'\\x00'*8\n"
        "    buf = ctypes.create_string_buffer(sa, len(sa))\n"
        "    # set a recv timeout-ish: we just attempt the connect (blocking)\n"
        "    rc = libc.connect(fd, buf, len(sa))\n"
        "    err = ctypes.get_errno()\n"
        "    libc.close(fd)\n"
        "    result = f'libc_connect_rc={rc};errno={err}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 9. http.client low-level (separate code path from urllib.request)
    "http_client": (
        "import http.client\n"
        "try:\n"
        "    c = http.client.HTTPConnection('example.com', 80, timeout=6)\n"
        "    c.request('GET', '/')\n"
        "    r = c.getresponse()\n"
        "    result = f'REACHED:{r.status}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
    # 10. allowlist-bypass via a host whose suffix collides with an allowed suffix
    #     (endswith logic). 'evilgoogleapis.com' should NOT match 'googleapis.com'
    #     if the guard requires a dot-boundary — but the guard ALSO has a bare
    #     endswith(suffix), so test whether a crafted hostname slips through.
    "allowlist_suffix_collision": (
        "import socket\n"
        "from infra_executor import _host_allowed\n" if False else
        "import socket\n"
        "tests = {}\n"
        "for h in ['notgoogleapis.com','evil-googleapis.com','googleapis.com.evil.com','xmongodb.net']:\n"
        "    try:\n"
        "        socket.create_connection((h, 80), timeout=3)\n"
        "        tests[h] = 'REACHED_OR_DNS_OK'\n"
        "    except Exception as e:\n"
        "        tests[h] = type(e).__name__\n"
        "result = tests\n"
    ),
    # 11. re-import socket fresh and grab the ORIGINAL connect off a new socket
    #     instance — the guard patches the class method, so even a fresh instance
    #     is patched. But test rebinding via socket.socket.__init__ tricks.
    "rebind_socket_attr": (
        "import socket\n"
        "try:\n"
        "    orig = getattr(socket.socket, 'connect')\n"
        "    s = socket.socket()\n"
        "    s.settimeout(5)\n"
        "    # try to call the C-level method via the type's __dict__ if present\n"
        "    result = f'connect_is={orig.__qualname__ if hasattr(orig,\"__qualname__\") else repr(orig)[:60]}'\n"
        "except Exception as e:\n"
        "    result = f'{type(e).__name__}:{e}'\n"
    ),
}


def classify(vector: str, env: dict) -> dict:
    """Classify a vector outcome."""
    status = env.get("status")
    rv = env.get("result", {})
    val = rv.get("value") if isinstance(rv, dict) else rv
    sval = str(val)
    blocked_in_proc = "SandboxNetworkBlocked" in sval or status == "blocked"
    reached = "REACHED" in sval
    return {
        "vector": vector,
        "status": status,
        "result_value": val,
        "blocked_by_in_process_guard": blocked_in_proc,
        "reached_internet": reached,
    }


def main() -> int:
    results = []
    for name, code in VECTORS.items():
        env = run_sandbox_local(code, {}, timeout_seconds=30)
        c = classify(name, env)
        c["full_envelope_status"] = env.get("status")
        c["error"] = env.get("error")
        results.append(c)
        print(f"=== {name} ===")
        print(f"  status={env.get('status')}  result={c['result_value']}")
        print(f"  blocked_in_process={c['blocked_by_in_process_guard']}  reached={c['reached_internet']}")
        print()

    print("==== SUMMARY JSON ====")
    print(json.dumps(results, indent=2, default=str))
    # Any reached_internet=True is a guard failure for that vector.
    leaks = [r["vector"] for r in results if r["reached_internet"]]
    print(f"\nIN-PROCESS-GUARD LEAKS (reached internet): {leaks}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
