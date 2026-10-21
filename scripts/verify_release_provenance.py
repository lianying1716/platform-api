#!/usr/bin/env python3
"""Read-only release provenance check for OPS-14.

This script never tags, pushes, creates a release or upgrades a deployment.  It
compares the facts that already exist after a release:

* the annotated tag resolves to a commit;
* the published GHCR main-image manifest digest resolves to OCI labels;
* the running instance reports the same version, full commit and image source.

Usage:
    python scripts/verify_release_provenance.py \
      --repo lianying1716/platform-api \
      --tag v1.2.3 \
      --image ghcr.io/lianying1716/platform-api \
      --version-url http://127.0.0.1:9100/api/system/version

The command exits non-zero on any mismatch.  It requires Docker credentials for
the image inspect and network access for the GitHub Release check.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.request
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Provenance:
    tag: str
    commit: str
    image: str
    image_digest: str
    image_commit: str
    image_version: str
    running_version: str
    running_commit: str
    running_source: str


def _run(*args: str, timeout: int = 30) -> str:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"{' '.join(args)} failed: {(result.stderr or result.stdout).strip()[-400:]}")
    return result.stdout.strip()


def github_tag_commit(repo: str, tag: str) -> str:
    """Resolve lightweight or annotated tags to the final commit SHA.

    ``object.sha`` on an annotated tag is the tag object, not a commit.  The
    updater already dereferences this in ``fetch_tag_commit``; the checker must
    use the same rule or it can never validate a normal release tag.
    """
    raw = _run("gh", "api", f"repos/{repo}/git/ref/tags/{tag}")
    obj = json.loads(raw).get("object") or {}
    if obj.get("type") == "tag":
        tag_object = json.loads(_run("gh", "api", str(obj.get("url") or "")))
        obj = tag_object.get("object") or obj
    sha = str(obj.get("sha") or "").strip()
    return sha if re.fullmatch(r"[0-9a-fA-F]{40}", sha) else ""


def image_labels(image_ref: str) -> dict:
    """Read the amd64 image config from a multi-arch GHCR manifest.

    Docker's ``{{json .}}`` puts the index under ``manifest``; ``{{json
    .Image}}`` is a platform-keyed map, not one image.  Taking ``manifests[0]``
    or ``.config`` at the top level silently produced empty facts.  Prefer the
    linux/amd64 entry because that is what the reference NAS runs; fall back to
    the index digest for single-platform images.
    """
    payload = json.loads(
        _run("docker", "buildx", "imagetools", "inspect", image_ref, "--format", "{{json .}}", timeout=90)
    )
    index = payload.get("manifest") or payload
    manifests = index.get("manifests") or []
    digest = index.get("digest") or ""
    selected = next(
        (
            item
            for item in manifests
            if (item.get("platform") or {}).get("os") == "linux"
            and (item.get("platform") or {}).get("architecture") == "amd64"
        ),
        None,
    )
    if selected is not None:
        digest = str(selected.get("digest") or digest)

    configs = json.loads(
        _run("docker", "buildx", "imagetools", "inspect", image_ref, "--format", "{{json .Image}}", timeout=90)
    )
    if isinstance(configs, dict) and "linux/amd64" in configs:
        image_config = configs["linux/amd64"]
    elif isinstance(configs, list):
        image_config = configs[0] if configs else {}
    else:
        image_config = configs
    labels = (image_config.get("config") or {}).get("Labels") or {}
    return {"digest": digest, "labels": labels}


def version_endpoint(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"version endpoint HTTP {response.status}")
        return json.loads(response.read().decode("utf-8"))


def verify(repo: str, tag: str, image: str, version_url: str) -> dict:
    tag_commit = github_tag_commit(repo, tag)
    image_ref = f"{image}:{tag}"
    image_info = image_labels(image_ref)
    labels = image_info["labels"]
    image_commit = str(labels.get("org.opencontainers.image.revision") or "")
    image_version = str(labels.get("org.opencontainers.image.version") or "")
    running = version_endpoint(version_url)
    facts = Provenance(
        tag=tag,
        commit=tag_commit,
        image=image_ref,
        image_digest=image_info["digest"],
        image_commit=image_commit,
        image_version=image_version,
        running_version=str(running.get("app_version") or ""),
        running_commit=str(running.get("git_commit") or ""),
        running_source=str(running.get("version_source") or ""),
    )
    errors: list[str] = []
    if not re.fullmatch(r"[0-9a-f]{40}", facts.commit):
        errors.append("GitHub tag 未解析为完整 commit")
    if not facts.image_digest.startswith("sha256:"):
        errors.append("镜像没有 digest")
    if facts.image_commit and not facts.commit.startswith(facts.image_commit[:12]):
        errors.append("镜像 OCI revision 与 tag commit 不一致")
    if facts.running_version != tag:
        errors.append(f"运行版本 {facts.running_version!r} != tag {tag!r}")
    if facts.running_commit and not facts.commit.startswith(facts.running_commit[:12]):
        errors.append("运行 commit 与 tag commit 不一致")
    if facts.running_source != "release_image":
        errors.append("运行实例不是发行镜像")
    return {"passed": not errors, "errors": errors, "facts": asdict(facts)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version-url", required=True)
    args = parser.parse_args()
    try:
        report = verify(args.repo, args.tag, args.image, args.version_url)
    except Exception as exc:  # noqa: BLE001 CLI must return a clear failure, not a traceback
        print(json.dumps({"passed": False, "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
