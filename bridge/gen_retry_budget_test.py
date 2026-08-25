#!/usr/bin/env python3
"""Check that RETRY_ATTEMPTS actually changes how many times a store write is
attempted, by lifting withRetry() out of server.js and running it.

Reading the code is not enough here: the value is read from the environment
at module load, forwarded through docker-compose, and reported back through
/anchor-config, and a break anywhere along that chain would leave the
ablation silently running with the default budget -- which is exactly the
class of failure that has cost this campaign three runs.
"""
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "app" / "server.js"


def lift(name: str, src: str) -> str:
    """Extract one top-level declaration verbatim.

    Brace counting alone is not enough: `const X = (() => { ... })();` closes
    its last bracket several characters before the statement ends, and a
    lifter that stops there swallows the start of the next declaration. We
    therefore scan for the terminating semicolon that sits at bracket depth
    zero, which is where the statement actually ends.
    """
    m = re.search(rf"^(const {name} = |(?:async )?function {name}\()", src, re.M)
    if not m:
        raise SystemExit(f"{name} not found in server.js")
    i = m.start()
    # `seen` guards against ending on a bracket that never opened; but a
    # declaration like `const X = 50;` contains no brackets at all, so the
    # first semicolon at depth zero ends it whether or not one was seen.
    depth, j, instr, esc, seen = 0, i, None, False, False
    while j < len(src):
        ch = src[j]
        if instr:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == instr:
                instr = None
        elif ch in "'\"`":
            instr = ch
        elif ch in "([{":
            depth += 1
            seen = True
        elif ch in ")]}":
            depth -= 1
        elif ch == ";" and depth == 0:
            return src[i:j + 1]
        elif ch == "\n" and seen and depth == 0:
            # a function declaration ends at its closing brace, with no
            # semicolon; the newline after it is the end of the statement
            return src[i:j]
        j += 1
    raise SystemExit(f"could not find the end of {name}")


def run(retry_env, fail_times):
    """Run withRetry against a function that fails `fail_times` then succeeds."""
    src = SERVER.read_text(encoding="utf-8")
    harness = "\n".join([
        lift("MAX_ATTEMPTS", src),
        lift("BASE_BACKOFF_MS", src),
        lift("sleep", src),
        lift("withRetry", src),
        f"""
let calls = 0;
const target = {fail_times};
withRetry(async () => {{
  calls += 1;
  if (calls <= target) throw new Error('injected');
}}).then(r => {{
  console.log(JSON.stringify({{ maxAttempts: MAX_ATTEMPTS, calls, ok: r.ok, attempts: r.attempts }}));
}});
""",
    ])
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
    if retry_env is not None:
        env["RETRY_ATTEMPTS"] = retry_env
    out = subprocess.run(["node", path], capture_output=True, text=True, env=env)
    if out.returncode != 0:
        return {"exit": out.returncode, "stderr": out.stderr.strip()[:200]}
    return json.loads(out.stdout.strip())


CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append((name, bool(cond), detail))


# --- the default is unchanged, so every previously reported result stands
r = run(None, fail_times=0)
check("default budget is 3", r.get("maxAttempts") == 3, r)
r = run(None, fail_times=2)
check("default retries twice and succeeds on the third",
      r.get("ok") is True and r.get("calls") == 3, r)
r = run(None, fail_times=3)
check("default gives up after three failures",
      r.get("ok") is False and r.get("calls") == 3, r)

# --- RETRY_ATTEMPTS=1 disables the retry
r = run("1", fail_times=0)
check("RETRY_ATTEMPTS=1 is honoured", r.get("maxAttempts") == 1, r)
r = run("1", fail_times=1)
check("with budget 1 a single failure is final: one call, not ok",
      r.get("ok") is False and r.get("calls") == 1, r)
r = run("1", fail_times=0)
check("with budget 1 a success still succeeds in one call",
      r.get("ok") is True and r.get("calls") == 1, r)

# --- an explicit 3 behaves exactly like the default
r = run("3", fail_times=2)
check("RETRY_ATTEMPTS=3 matches the default", r.get("ok") is True and r.get("calls") == 3, r)

# --- a malformed value must stop the bridge rather than fall back silently
for bad in ("0", "-1", "two", "2.5"):
    r = run(bad, fail_times=0)
    check(f"RETRY_ATTEMPTS={bad!r} refuses to start", r.get("exit") == 1, r)

# --- an empty value falls back to the default, since compose may pass ""
r = run("", fail_times=0)
check("empty RETRY_ATTEMPTS falls back to 3", r.get("maxAttempts") == 3, r)

# --- the value must be forwarded by compose, or the container never sees it
compose = (Path(__file__).resolve().parent.parent / "docker-compose.yml").read_text()
check("docker-compose forwards RETRY_ATTEMPTS",
      re.search(r'RETRY_ATTEMPTS:\s*"\$\{RETRY_ATTEMPTS:-3\}"', compose) is not None)

# --- and reported back, or a run cannot identify itself
check("/anchor-config reports retryAttempts",
      "retryAttempts: MAX_ATTEMPTS" in SERVER.read_text(encoding="utf-8"))

# --- and recorded per trial, or the columns cannot be told apart afterwards
harness_py = (Path(__file__).resolve().parent.parent /
              "experiments" / "run_experiment.py").read_text()
check("the harness records retry_attempts per trial",
      '"retry_attempts": anchor_cfg.get("retryAttempts")' in harness_py)

width = max(len(n) for n, _, _ in CHECKS)
failed = 0
for name, ok, detail in CHECKS:
    print(f"  {'PASS' if ok else 'FAIL'}  {name:<{width}}" + ("" if ok else f"   {detail}"))
    failed += not ok
print()
if failed:
    print(f"{failed} of {len(CHECKS)} CHECKS FAILED")
    sys.exit(1)
print(f"ALL {len(CHECKS)} RETRY-BUDGET CHECKS PASSED")
