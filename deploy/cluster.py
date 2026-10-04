"""3-laptop cluster tooling: generate per-laptop .env files, build the L2/L3 bundles, verify the running cluster.

    python deploy/cluster.py gen       # deploy/cluster.env -> deploy/out/{L1,L2,L3}.env + L3 apps/terminal .env.local
    python deploy/cluster.py bundle    # deploy/out/L2_bundle(.zip), deploy/out/L3_bundle(.zip)
    python deploy/cluster.py verify    # ping every service + Ollama, check key auth, print a pass/fail table

The .ps1 files next to this are thin wrappers. deploy/out/ holds secrets (CLUSTER_KEY, SMTP); it is git-ignored.
"""
from __future__ import annotations

import argparse
import ipaddress
import re
import secrets
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"
OUT = DEPLOY / "out"
CLUSTER_ENV = DEPLOY / "cluster.env"
sys.path.insert(0, str(ROOT / "infra"))

# env key -> (default port, laptop) ; the uvicorn port each service binds (infra/run_all_local.ps1 reads *_PORT)
PORTS = {"ORCH_PORT": (8000, "L1"), "QUANT_PORT": (8101, "L2"), "SENTIMENT_PORT": (8102, "L2"),
         "AGRI_PORT": (8103, "L2"), "VECTOR_PORT": (8104, "L2"), "INGEST_PORT": (8201, "L3"),
         "MONITOR_PORT": (8202, "L3")}
URL_KEYS = {"ORCH_URL": "ORCH_PORT", "QUANT_URL": "QUANT_PORT", "SENTIMENT_URL": "SENTIMENT_PORT",
            "AGRI_URL": "AGRI_PORT", "VECTOR_URL": "VECTOR_PORT", "INGEST_URL": "INGEST_PORT",
            "MONITOR_URL": "MONITOR_PORT"}
SERVICES = {"orchestrator": "ORCH_PORT", "quant": "QUANT_PORT", "sentiment": "SENTIMENT_PORT", "agri": "AGRI_PORT",
            "vectordb": "VECTOR_PORT", "ingestion": "INGEST_PORT", "monitor": "MONITOR_PORT"}
PLACEHOLDER_KEYS = {"", "change-me", "changeme", "secret", "xxx"}


def rel(p) -> str:
    p = Path(p)
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


class ConfigError(ValueError):
    pass


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).split(" #")[0].strip()
    return out


def load_cluster(path: Path = CLUSTER_ENV, allow_loopback: bool = False) -> dict[str, str]:
    """Read and validate deploy/cluster.env. Missing / placeholder / duplicate IPs are an error, never guessed."""
    if not path.exists():
        raise ConfigError(f"{path} not found: copy deploy/cluster.env.example to deploy/cluster.env and fill in the IPs")
    c = read_env(path)
    ips = []
    for lap in ("L1", "L2", "L3"):
        raw = c.get(f"{lap}_IP", "")
        if not raw or re.search(r"[xX*<>]", raw):
            raise ConfigError(f"{lap}_IP is missing or a placeholder ({raw!r}); set the real LAN IP from `ipconfig`")
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            raise ConfigError(f"{lap}_IP={raw!r} is not an IP address") from None
        if ip.is_unspecified or ip.is_multicast or (ip.is_loopback and not allow_loopback):
            raise ConfigError(f"{lap}_IP={raw} cannot be reached from the other laptops")
        ips.append(raw)
    if len(set(ips)) < 3 and not allow_loopback:
        raise ConfigError(f"the three IPs must differ (got {', '.join(ips)})")
    for k, (default, _) in PORTS.items():
        v = c.get(k) or str(default)
        if not v.isdigit() or not 1 <= int(v) <= 65535:
            raise ConfigError(f"{k}={v!r} is not a port")
        c[k] = v
    c["MOCK"] = c.get("MOCK") or "0"
    c["DEMO_MODE"] = c.get("DEMO_MODE") or "0"
    return c


def ensure_key(c: dict[str, str], path: Path = CLUSTER_ENV) -> str:
    key = c.get("CLUSTER_KEY", "")
    if key.lower() in PLACEHOLDER_KEYS:
        key = secrets.token_hex(16)
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^CLUSTER_KEY=.*$", f"CLUSTER_KEY={key}", text, flags=re.M) if re.search(
            r"^CLUSTER_KEY=", text, flags=re.M) else text + f"\nCLUSTER_KEY={key}\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"generated a new CLUSTER_KEY and saved it in {rel(path)}")
    elif len(key) < 16:
        raise ConfigError("CLUSTER_KEY must be at least 16 characters (or empty to generate one)")
    c["CLUSTER_KEY"] = key
    return key


def render_laptop(c: dict[str, str], laptop: str, secrets_from: dict[str, str]) -> str:
    """infra/make_env.render with this cluster's IPs, then ports, key, MOCK/DEMO and secrets from the root .env."""
    from make_env import render
    base = (ROOT / ".env.example").read_text(encoding="utf-8")
    s = render(base, (c["L1_IP"], c["L2_IP"], c["L3_IP"]), laptop, int(c["MOCK"]))
    for url_key, port_key in URL_KEYS.items():
        s = re.sub(rf"^({url_key}=http://\$\{{L[123]_HOST\}}):\d+", rf"\g<1>:{c[port_key]}", s, flags=re.M)
    s = re.sub(r"^CLUSTER_KEY=.*$", f"CLUSTER_KEY={c['CLUSTER_KEY']}", s, flags=re.M)
    s = re.sub(r"^DEMO_MODE=.*$", f"DEMO_MODE={c['DEMO_MODE']}", s, flags=re.M)
    # secrets (LLM boost keys, SMTP, Telegram, Earthdata): empty in the template, copied from this machine's .env
    for k, v in secrets_from.items():
        if v and k not in {"CLUSTER_KEY", "MOCK", "DEMO_MODE", "THIS_LAPTOP"} and not k.endswith("_HOST"):
            s = re.sub(rf"^{k}=\s*(#.*)?$", lambda _m, k=k, v=v: f"{k}={v}", s, flags=re.M)
    s += "\n# service ports (infra/run_all_local.ps1 binds these)\n" + "".join(f"{k}={c[k]}\n" for k in PORTS)
    return s


def ui_env(c: dict[str, str]) -> str:
    l1, l2, l3 = c["L1_IP"], c["L2_IP"], c["L3_IP"]
    return ("# generated by deploy/gen_cluster.ps1: the terminal on L3 talks to every laptop directly\n"
            f"NEXT_PUBLIC_ORCH_URL=http://{l1}:{c['ORCH_PORT']}\n"
            f"NEXT_PUBLIC_MONITOR_URL=http://{l3}:{c['MONITOR_PORT']}\n"
            f"NEXT_PUBLIC_QUANT_URL=http://{l2}:{c['QUANT_PORT']}\n"
            f"NEXT_PUBLIC_INGEST_URL=http://{l3}:{c['INGEST_PORT']}\n"
            f"NEXT_PUBLIC_VECTOR_URL=http://{l2}:{c['VECTOR_PORT']}\n"
            f"NEXT_PUBLIC_CLUSTER_KEY={c['CLUSTER_KEY']}\n")


def cmd_gen(args) -> int:
    c = load_cluster(Path(args.config), allow_loopback=args.allow_loopback)
    ensure_key(c, Path(args.config))
    OUT.mkdir(parents=True, exist_ok=True)
    root_env = read_env(ROOT / ".env")
    for lap in ("L1", "L2", "L3"):
        (OUT / f"{lap}.env").write_text(render_laptop(c, lap, root_env), encoding="utf-8", newline="\n")
    (OUT / "L3.env.local").write_text(ui_env(c), encoding="utf-8", newline="\n")
    print(f"wrote deploy/out/L1.env L2.env L3.env L3.env.local  (L1 {c['L1_IP']}  L2 {c['L2_IP']}  L3 {c['L3_IP']})")
    print("on L1 (this repo):  copy deploy\\out\\L1.env .env   then  deploy\\make_bundles.ps1")
    return 0


# ---------------------------------------------------------------- bundles
SKIP_DIRS = {"__pycache__", ".pytest_cache", "node_modules", ".next", ".next-build", "out", "dist", ".venv", "logs",
             "cache", "portfolios", "backtest_runs"}
SKIP_FILES = re.compile(r"(\.db(-shm|-wal)?|\.pyc|\.log|\.err|^\.env(\.bak)?|^\.env\.local)$")
CURATED_DATA_SKIP = {"logs", "cache", "portfolios", "backtest"}
BUNDLES = {
    "L2": {"paths": ["packages", "services/__init__.py", "services/quant", "services/sentiment", "services/agri",
                     "services/vectordb", "backtest", "infra", "requirements.txt"],
           "reqs": ["services/quant", "services/sentiment", "services/agri", "services/vectordb"],
           "models": ["OLLAMA_MODEL_L2"], "docker": True, "frontend": False},
    "L3": {"paths": ["packages", "services/__init__.py", "services/ingestion", "services/monitor", "apps/terminal",
                     "infra", "requirements.txt"],
           "reqs": ["services/ingestion", "services/monitor"],
           "models": ["OLLAMA_MODEL_L3_FAST", "OLLAMA_MODEL_L3_RED"], "docker": False, "frontend": True},
}


def _copy(src: Path, dst: Path) -> None:
    if src.is_file():
        if not SKIP_FILES.search(src.name):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
        return
    for p in src.iterdir():
        if p.is_dir() and p.name in SKIP_DIRS:
            continue
        _copy(p, dst / p.name)


def _scripts(lap: str, spec: dict, env: dict[str, str]) -> dict[str, str]:
    reqs = " ".join(f'-r "{r}\\requirements.txt"' for r in spec["reqs"])
    models = [env.get(m, "") for m in spec["models"]]
    pulls = "\n".join(f"    ollama pull {m}" for m in models if m)
    setup = f"""# One-time setup for {lap}. Run in this folder:  powershell -ExecutionPolicy Bypass -File setup_{lap}.ps1 [-PullModels]
param([switch]$PullModels)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {{ python -m venv .venv }}
.venv\\Scripts\\python -m pip install --upgrade pip
.venv\\Scripts\\python -m pip install -r requirements.txt {reqs}
if ($PullModels) {{
{pulls}
}} else {{ Write-Host "models this laptop serves: {', '.join(models)}  (re-run with -PullModels, or ollama pull <tag>)" }}
"""
    if spec["docker"]:
        setup += "docker compose -f infra\\docker-compose.yml up -d   # Weaviate :8080\n"
    if spec["frontend"]:
        setup += "Push-Location apps\\terminal; npm install; Pop-Location\n"
    setup += ('Write-Host "Open the firewall once (Admin PowerShell): powershell -ExecutionPolicy Bypass -File infra\\firewall.ps1"\n'
              f'Write-Host "setup done. start: .\\start_{lap}.ps1" -ForegroundColor Green\n')
    start = (f"# Start {lap}'s services (and on L3 the desktop app).  .\\start_{lap}.ps1\n"
             + ("docker compose -f \"$PSScriptRoot\\infra\\docker-compose.yml\" up -d\n" if spec["docker"] else "")
             + f"powershell -ExecutionPolicy Bypass -File \"$PSScriptRoot\\infra\\run_all_local.ps1\" -Laptop {lap} @args\n")
    stop = f"powershell -ExecutionPolicy Bypass -File \"$PSScriptRoot\\infra\\run_all_local.ps1\" -Stop\n"
    health = f"& \"$PSScriptRoot\\.venv\\Scripts\\python.exe\" \"$PSScriptRoot\\infra\\check_health.py\" @args\n"
    return {f"setup_{lap}.ps1": setup, f"start_{lap}.ps1": start, f"stop_{lap}.ps1": stop, f"health_{lap}.ps1": health}


def _readme(lap: str, c: dict[str, str]) -> str:
    role = {"L2": "Compute: quant :{QUANT_PORT}, sentiment :{SENTIMENT_PORT}, agri :{AGRI_PORT}, vectordb :{VECTOR_PORT}, "
                  "Weaviate :8080, Ollama :11434",
            "L3": "Edge: ingestion :{INGEST_PORT}, monitor :{MONITOR_PORT}, the desktop app (Next.js :3000 + Electron), "
                  "Ollama :11434"}[lap].format(**c)
    return f"""# {lap} bundle

{role}. Generated by deploy/make_bundles.ps1 on L1 for the cluster L1 {c['L1_IP']}, L2 {c['L2_IP']}, L3 {c['L3_IP']}.

1. Unzip anywhere (a path without spaces is safest). Install Python 3.11+{', Docker Desktop' if lap == 'L2' else ', Node 20+'} and Ollama.
2. Admin PowerShell, once: `powershell -ExecutionPolicy Bypass -File infra\\firewall.ps1`. The Wi-Fi profile must be Private.
3. `powershell -ExecutionPolicy Bypass -File setup_{lap}.ps1 -PullModels`
4. `.\\start_{lap}.ps1`  (start order for the cluster: L2, then L3, then L1)
5. `.\\health_{lap}.ps1` shows every service the laptop can reach. `.\\stop_{lap}.ps1` stops everything.

`.env` here already holds the cluster IPs and CLUSTER_KEY. If the hotspot gives new IPs, regenerate the bundles on
L1 (deploy/gen_cluster.ps1, then deploy/make_bundles.ps1) rather than editing by hand.
"""


def cmd_bundle(args) -> int:
    c = load_cluster(Path(args.config), allow_loopback=args.allow_loopback)
    if not (OUT / "L2.env").exists():
        raise ConfigError("run deploy/gen_cluster.ps1 first (deploy/out/L2.env missing)")
    c["CLUSTER_KEY"] = read_env(OUT / "L2.env").get("CLUSTER_KEY", "")
    for lap, spec in BUNDLES.items():
        dst = OUT / f"{lap}_bundle"
        if dst.exists():
            shutil.rmtree(dst)
        for part in spec["paths"]:
            _copy(ROOT / part, dst / part)
        for p in (ROOT / "data").iterdir():  # curated inputs only; runtime dbs / caches stay behind
            if p.name not in CURATED_DATA_SKIP:
                _copy(p, dst / "data" / p.name)
        env = read_env(OUT / f"{lap}.env")
        shutil.copy2(OUT / f"{lap}.env", dst / ".env")
        if spec["frontend"]:
            shutil.copy2(OUT / "L3.env.local", dst / "apps" / "terminal" / ".env.local")
        for name, text in _scripts(lap, spec, env).items():
            (dst / name).write_text(text, encoding="utf-8")
        (dst / "README.md").write_text(_readme(lap, c), encoding="utf-8")
        zip_path = shutil.make_archive(str(dst), "zip", root_dir=dst)
        size = Path(zip_path).stat().st_size / 1e6
        print(f"{lap}: {rel(dst)}  ->  {rel(zip_path)} ({size:.1f} MB)")
    print("copy each zip to its laptop (USB / shared folder). They contain CLUSTER_KEY and SMTP secrets: do not post them.")
    return 0


# ---------------------------------------------------------------- verify
def verify(c: dict[str, str], timeout: float = 3.0) -> list[dict]:
    """One row per check. Auth probe: a path that does not exist must give 401 without the key, and 404 (passed auth,
    no route) with it. /health must stay open without the key."""
    import httpx
    key = c.get("CLUSTER_KEY", "")
    ip = {"L1": c["L1_IP"], "L2": c["L2_IP"], "L3": c["L3_IP"]}
    rows: list[dict] = []
    for name, port_key in SERVICES.items():
        lap = PORTS[port_key][1]
        base = f"http://{ip[lap]}:{c[port_key]}"
        row = {"check": name, "laptop": lap, "url": base}
        try:
            with httpx.Client(timeout=timeout) as cl:
                h = cl.get(f"{base}/health")
                row["health"] = h.json().get("status", "?") if h.status_code == 200 else f"HTTP {h.status_code}"
                if key:
                    no = cl.get(f"{base}/__auth_probe").status_code
                    yes = cl.get(f"{base}/__auth_probe", headers={"X-Cluster-Key": key}).status_code
                    row["auth"] = "ok" if (no == 401 and yes != 401) else f"FAIL (no key {no}, key {yes})"
                else:
                    row["auth"] = "off"
        except httpx.HTTPError as e:
            row["health"], row["auth"] = f"unreachable ({type(e).__name__})", "-"
        row["pass"] = row["health"] in ("ok", "degraded") and row["auth"] in ("ok", "off")
        rows.append(row)
    want = {"L1": [c.get("OLLAMA_MODEL_L1")], "L2": [c.get("OLLAMA_MODEL_L2")],
            "L3": [c.get("OLLAMA_MODEL_L3_FAST"), c.get("OLLAMA_MODEL_L3_RED")]}
    for lap in ("L1", "L2", "L3"):
        base = f"http://{ip[lap]}:11434"
        row = {"check": "ollama", "laptop": lap, "url": base, "auth": "-"}
        try:
            tags = {m["name"] for m in httpx.get(f"{base}/api/tags", timeout=timeout).json().get("models", [])}
            missing = [m for m in want[lap] if m and m not in tags and f"{m}:latest" not in tags]
            row["health"] = "ok" if not missing else f"missing {', '.join(missing)}"
        except (httpx.HTTPError, ValueError) as e:
            row["health"] = f"unreachable ({type(e).__name__})"
        row["pass"] = row["health"] == "ok"
        rows.append(row)
    return rows


def cmd_verify(args) -> int:
    c = load_cluster(Path(args.config), allow_loopback=args.allow_loopback)
    models = read_env(ROOT / ".env.example")
    for k in ("OLLAMA_MODEL_L1", "OLLAMA_MODEL_L2", "OLLAMA_MODEL_L3_FAST", "OLLAMA_MODEL_L3_RED"):
        c.setdefault(k, read_env(OUT / "L1.env").get(k) or models.get(k, ""))
    rows = verify(c)
    print(f"{'CHECK':13} {'LAPTOP':6} {'URL':28} {'HEALTH':32} {'KEY AUTH':26} RESULT")
    for r in rows:
        print(f"{r['check']:13} {r['laptop']:6} {r['url']:28} {r['health']:32} {r['auth']:26} {'PASS' if r['pass'] else 'FAIL'}")
    failed = [r for r in rows if not r["pass"]]
    print(f"\n{len(rows) - len(failed)}/{len(rows)} checks passed")
    if any("unreachable" in r["health"] for r in failed):
        print("unreachable: is the laptop on, on the same Wi-Fi, network profile Private, infra\\firewall.ps1 run, service started?")
    if any(r["health"].startswith("missing") for r in failed):
        print("missing models: on that laptop run  ollama pull <tag>  (setup_Lx.ps1 -PullModels does it)")
    if any(r["auth"].startswith("FAIL") for r in failed):
        print("key auth failed: that laptop's .env has a different CLUSTER_KEY; regenerate the bundles and restart it")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["gen", "bundle", "verify"])
    ap.add_argument("--config", default=str(CLUSTER_ENV))
    ap.add_argument("--allow-loopback", action="store_true", help="allow 127.0.0.1 (testing the scripts on one laptop)")
    args = ap.parse_args(argv)
    try:
        return {"gen": cmd_gen, "bundle": cmd_bundle, "verify": cmd_verify}[args.cmd](args)
    except ConfigError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
