"""Tests for .github/scripts/pin-actions.py.

Repository tooling rather than application code, so it lives outside
tests/unit/ (which mirrors app/) and outside the coverage gate. It still gets
tested, because this tool rewrites workflow files in place.

Everything here is offline: no GitHub API calls.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "pin-actions.py"


def _load():
    """Import the script by path — its filename is not a valid module name."""
    spec = importlib.util.spec_from_file_location("pin_actions", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules["pin_actions"] = module
    spec.loader.exec_module(module)
    return module


pin = _load()

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
SHA2 = "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a"


def write_workflow(root: Path, body: str, name: str = "ci.yml") -> Path:
    path = root / ".github" / "workflows" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


# --- parsing --------------------------------------------------------------

@pytest.mark.parametrize("line,repo,subpath,ref", [
    ("      - uses: actions/checkout@v4", "actions/checkout", "", "v4"),
    ("  uses: actions/setup-python@v5", "actions/setup-python", "", "v5"),
    (f"      - uses: actions/checkout@{SHA} # v7.0.1", "actions/checkout", "", SHA),
    ('      - uses: "actions/checkout@v4"', "actions/checkout", "", "v4"),
    ("      - uses: 'actions/checkout@v4'", "actions/checkout", "", "v4"),
    ("      - uses: github/codeql-action/init@v3", "github/codeql-action", "/init", "v3"),
    ("      - uses: owner/repo@main", "owner/repo", "", "main"),
])
def test_recognises_uses_lines(tmp_path, line, repo, subpath, ref):
    path = write_workflow(tmp_path, f"jobs:\n  x:\n    steps:\n{line}\n")
    found = pin.find_uses(path)
    assert len(found) == 1
    assert (found[0].repo, found[0].subpath, found[0].ref) == (repo, subpath, ref)


@pytest.mark.parametrize("line", [
    "      - uses: ./.github/actions/local",       # local action: no commit exists
    "      - uses: docker://alpine:3.20",          # docker image, not a repo
    "      - run: echo uses: actions/checkout@v4",  # a run step, not a uses
    "      # - uses: actions/checkout@v4",          # commented out
    "      - uses: actions/checkout",               # no ref at all
])
def test_ignores_lines_that_cannot_be_pinned(tmp_path, line):
    path = write_workflow(tmp_path, f"jobs:\n  x:\n    steps:\n{line}\n")
    assert pin.find_uses(path) == []


def test_is_pinned_detects_sha_refs(tmp_path):
    path = write_workflow(tmp_path, (
        "steps:\n"
        "  - uses: actions/checkout@v4\n"
        f"  - uses: actions/setup-python@{SHA}\n"
    ))
    unpinned, pinned = pin.find_uses(path)
    assert unpinned.is_pinned is False
    assert pinned.is_pinned is True


def test_line_numbers_are_reported(tmp_path):
    path = write_workflow(tmp_path, "a\nb\n  - uses: actions/checkout@v4\n")
    assert pin.find_uses(path)[0].lineno == 3


# --- rewriting ------------------------------------------------------------

def test_rewrite_preserves_indentation_and_adds_the_version_comment():
    line = "      - uses: actions/checkout@v4"
    out = pin.rewrite_line(line, "actions/checkout", "", SHA, "v7.0.1")
    assert out == f"      - uses: actions/checkout@{SHA} # v7.0.1"


def test_rewrite_keeps_a_subpath():
    line = "      - uses: github/codeql-action/init@v3"
    out = pin.rewrite_line(line, "github/codeql-action", "/init", SHA, "v3.1.0")
    assert out == f"      - uses: github/codeql-action/init@{SHA} # v3.1.0"


def test_rewrite_replaces_a_stale_comment_rather_than_appending():
    line = f"      - uses: actions/checkout@{SHA} # v4.2.2"
    out = pin.rewrite_line(line, "actions/checkout", "", SHA2, "v7.0.1")
    assert out.count("#") == 1
    assert out.endswith("# v7.0.1")


def test_rewrite_preserves_quoting():
    line = '      - uses: "actions/checkout@v4"'
    out = pin.rewrite_line(line, "actions/checkout", "", SHA, "v7.0.1")
    assert out == f'      - uses: "actions/checkout@{SHA}" # v7.0.1'


# --- version ordering -----------------------------------------------------

def test_newest_version_wins():
    tags = ["v1.0.0", "v4.2.2", "v10.0.0", "v4.10.0"]
    assert max(tags, key=pin._version_key) == "v10.0.0"


def test_prereleases_lose_to_the_final_release():
    assert max(["v2.0.0", "v2.0.0-rc1"], key=pin._version_key) == "v2.0.0"


def test_unparseable_tags_never_win():
    assert max(["v1.0.0", "latest", "nightly"], key=pin._version_key) == "v1.0.0"


def test_missing_components_are_treated_as_zero():
    assert pin._version_key("v3") < pin._version_key("v3.1")


# --- discovery ------------------------------------------------------------

def test_finds_workflows_and_composite_actions(tmp_path):
    write_workflow(tmp_path, "steps: []", "ci.yml")
    write_workflow(tmp_path, "steps: []", "release.yaml")
    composite = tmp_path / ".github" / "actions" / "setup" / "action.yml"
    composite.parent.mkdir(parents=True)
    composite.write_text("runs:\n  using: composite\n")

    names = {p.name for p in pin.workflow_files(tmp_path)}
    assert names == {"ci.yml", "release.yaml", "action.yml"}


def test_no_workflows_is_not_an_error(tmp_path):
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0


# --- --check (offline) ----------------------------------------------------

def test_check_fails_on_an_unpinned_action(tmp_path, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 1
    assert "not pinned" in capsys.readouterr().out


def test_check_passes_when_everything_is_pinned(tmp_path, capsys):
    write_workflow(tmp_path, f"steps:\n  - uses: actions/checkout@{SHA} # v7.0.1\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0
    assert "pinned to a commit SHA" in capsys.readouterr().out


def test_check_ignores_local_actions(tmp_path):
    write_workflow(tmp_path, "steps:\n  - uses: ./.github/actions/setup\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0


def test_check_makes_no_network_calls(tmp_path, monkeypatch):
    """--check must stay usable in CI without a token or the network."""
    def explode(*args, **kwargs):
        raise AssertionError("--check must not call the GitHub API")

    monkeypatch.setattr(pin, "_request", explode)
    write_workflow(tmp_path, f"steps:\n  - uses: actions/checkout@{SHA} # v7.0.1\n")
    assert pin.main(["--check", "--root", str(tmp_path)]) == 0


def test_check_does_not_modify_files(tmp_path):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    before = path.read_text()
    pin.main(["--check", "--root", str(tmp_path)])
    assert path.read_text() == before


# --- writing --------------------------------------------------------------

def test_pinning_rewrites_only_the_uses_line(tmp_path, monkeypatch):
    body = (
        "name: CI\n"
        "on: push\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Keep me\n"
        "        run: echo 'uses: actions/checkout@v4'\n"
    )
    path = write_workflow(tmp_path, body)

    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    assert pin.main(["--root", str(tmp_path)]) == 0

    lines = path.read_text().splitlines()
    assert lines[6] == f"      - uses: actions/checkout@{SHA} # v7.0.1"
    # Everything else, including a run step that merely mentions "uses:", is
    # left exactly as it was.
    assert lines[7] == "      - name: Keep me"
    assert lines[8] == "        run: echo 'uses: actions/checkout@v4'"
    assert path.read_text().endswith("\n"), "trailing newline must survive"


def test_dry_run_changes_nothing(tmp_path, monkeypatch):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    before = path.read_text()

    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    assert pin.main(["--dry-run", "--root", str(tmp_path)]) == 0
    assert path.read_text() == before


def test_keep_version_pins_without_upgrading(tmp_path, monkeypatch):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")

    def must_not_be_called(self, repo):
        raise AssertionError("--keep-version must not look up the latest release")

    monkeypatch.setattr(pin.Resolver, "latest_tag", must_not_be_called)
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    assert pin.main(["--keep-version", "--root", str(tmp_path)]) == 0
    assert path.read_text().strip().endswith(f"actions/checkout@{SHA} # v4")


def test_running_twice_is_idempotent(tmp_path, monkeypatch):
    path = write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    pin.main(["--root", str(tmp_path)])
    once = path.read_text()
    pin.main(["--root", str(tmp_path)])
    assert path.read_text() == once


def test_major_bump_is_reported(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v7.0.1")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    pin.main(["--dry-run", "--root", str(tmp_path)])
    out = capsys.readouterr().out
    assert "major version bump" in out.lower()
    assert "v4 -> v7.0.1" in out


def test_minor_bump_is_not_reported_as_major(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4.1.0\n")
    monkeypatch.setattr(pin.Resolver, "latest_tag", lambda self, repo: "v4.2.2")
    monkeypatch.setattr(pin.Resolver, "sha_for", lambda self, repo, ref: SHA)

    pin.main(["--dry-run", "--root", str(tmp_path)])
    assert "major version bump" not in capsys.readouterr().out.lower()


def test_api_failure_is_reported_not_crashed(tmp_path, monkeypatch, capsys):
    write_workflow(tmp_path, "steps:\n  - uses: actions/checkout@v4\n")

    def fail(self, repo):
        raise pin.PinError("GitHub API rate limit reached.")

    monkeypatch.setattr(pin.Resolver, "latest_tag", fail)
    assert pin.main(["--root", str(tmp_path)]) == 1
    assert "rate limit" in capsys.readouterr().err
