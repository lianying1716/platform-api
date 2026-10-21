"""OPS-14: release provenance checker is strict and read-only."""

import sys
from types import SimpleNamespace

from scripts import verify_release_provenance as provenance


COMMIT = "a" * 40


def _stub(monkeypatch, *, running=None, labels=None):
    monkeypatch.setattr(provenance, "github_tag_commit", lambda repo, tag: COMMIT)
    monkeypatch.setattr(
        provenance,
        "image_labels",
        lambda image: {
            "digest": "sha256:" + "b" * 64,
            "labels": labels if labels is not None else {
                "org.opencontainers.image.revision": COMMIT,
                "org.opencontainers.image.version": "v1.2.3",
            },
        },
    )
    monkeypatch.setattr(
        provenance,
        "version_endpoint",
        lambda url: running if running is not None else {
            "app_version": "v1.2.3", "git_commit": COMMIT, "version_source": "release_image",
        },
    )


def test_provenance_passes_only_when_tag_digest_commit_and_runtime_agree(monkeypatch):
    _stub(monkeypatch)
    report = provenance.verify("repo", "v1.2.3", "ghcr.io/x/api", "http://api/version")
    assert report["passed"] is True
    assert report["facts"]["image_digest"].startswith("sha256:")


def test_provenance_rejects_source_checkout_and_wrong_commit(monkeypatch):
    _stub(
        monkeypatch,
        running={"app_version": "v1.2.3", "git_commit": "c" * 40, "version_source": "source_checkout"},
    )
    report = provenance.verify("repo", "v1.2.3", "ghcr.io/x/api", "http://api/version")
    assert report["passed"] is False
    assert any("commit" in item for item in report["errors"])
    assert any("发行镜像" in item for item in report["errors"])


def test_provenance_rejects_wrong_image_revision(monkeypatch):
    _stub(
        monkeypatch,
        labels={
            "org.opencontainers.image.revision": "d" * 40,
            "org.opencontainers.image.version": "v1.2.3",
        },
    )
    report = provenance.verify("repo", "v1.2.3", "ghcr.io/x/api", "http://api/version")
    assert report["passed"] is False
    assert any("OCI revision" in item for item in report["errors"])


def test_annotated_tag_is_dereferenced_to_commit(monkeypatch):
    """The GitHub ref for a normal release is a tag object, not a commit."""
    import json

    responses = {
        "repos/o/r/git/ref/tags/v1.2.3": {
            "object": {"type": "tag", "sha": "1" * 40, "url": "https://api.github.com/repos/o/r/git/tags/1"}
        },
        "https://api.github.com/repos/o/r/git/tags/1": {"object": {"type": "commit", "sha": COMMIT}},
    }
    monkeypatch.setattr(provenance, "_run", lambda *args, **kwargs: json.dumps(responses[args[2]]))
    assert provenance.github_tag_commit("o/r", "v1.2.3") == COMMIT


def test_image_labels_uses_nested_manifest_and_amd64_config(monkeypatch):
    """Docker returns ``manifest`` and a platform-keyed ``.Image`` map."""
    import json

    payload = {
        "manifest": {
            "digest": "sha256:" + "0" * 64,
            "manifests": [
                {"digest": "sha256:" + "1" * 64, "platform": {"os": "linux", "architecture": "arm64"}},
                {"digest": "sha256:" + "2" * 64, "platform": {"os": "linux", "architecture": "amd64"}},
            ],
        }
    }
    configs = {
        "linux/amd64": {"config": {"Labels": {"org.opencontainers.image.revision": COMMIT}}},
        "linux/arm64": {"config": {"Labels": {"org.opencontainers.image.revision": "f" * 40}}},
    }

    def fake_run(*args, **kwargs):
        return json.dumps(payload if "{{json .}}" in args else configs)

    monkeypatch.setattr(provenance, "_run", fake_run)
    info = provenance.image_labels("ghcr.io/o/r:v1.2.3")
    assert info["digest"] == "sha256:" + "2" * 64
    assert info["labels"]["org.opencontainers.image.revision"] == COMMIT
