"""Verify the stdlib-only wheel in an isolated disposable Linux container."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import uuid

IMAGE = "python:3.11-slim@sha256:07d4824036af339fa1b6ef769cc99d9c10a141769538b52b3b597c84f7a7b535"


def run(*args):
    result = subprocess.run(list(map(str, args)), capture_output=True, encoding="utf-8", timeout=90)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {result.stderr[-2000:]}")
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    if not wheel.is_file() or not re.fullmatch(r"server_handbook-[0-9.]+-py3-none-any\.whl", wheel.name):
        raise SystemExit("Supply the local built Server Handbook wheel.")
    token = uuid.uuid4().hex
    name = "shbwheel_"+token
    container = None
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        container = run("docker", "run", "--pull", "never", "--detach", "--network", "none", "--name", name,
                        "--label", "server-handbook.verification="+token, IMAGE, "sleep", "600")
        assert re.fullmatch(r"[0-9a-f]{64}", container)
        label = run("docker", "inspect", "--format", '{{index .Config.Labels "server-handbook.verification"}}', container)
        assert label == token
        remote_wheel = "/tmp/"+wheel.name
        run("docker", "cp", wheel, container+":"+remote_wheel)
        run("docker", "cp", Path(__file__).with_name("verify_installed.py").resolve(), container+":/tmp/verify_installed.py")
        run("docker", "exec", container, "python", "-m", "venv", "/tmp/handbook-venv")
        executable = "/tmp/handbook-venv/bin/python"
        run("docker", "exec", container, executable, "-m", "pip", "install", "--no-index", remote_wheel)
        output = run("docker", "exec", container, executable, "/tmp/verify_installed.py", "--base-only", "--output", "/tmp/result.json")
        run("docker", "cp", container+":/tmp/result.json", args.output.resolve())
        evidence = json.loads(args.output.read_text(encoding="utf-8"))
        evidence.update(platform="Linux in isolated Debian-based container", image=IMAGE,
                        network="none", host_volumes=False, container_cleanup="same_verified_container_id")
        args.output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
        print(output)
    finally:
        if container is not None:
            label = run("docker", "inspect", "--format", '{{index .Config.Labels "server-handbook.verification"}}', container)
            assert label == token
            run("docker", "rm", "--force", container)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
