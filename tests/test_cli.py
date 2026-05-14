from __future__ import annotations

import pathlib

import pytest

from worknow import cli


# ---------- summarize_status ----------

def test_summarize_status_clean():
    assert cli.summarize_status("") == "clean"


def test_summarize_status_groups_by_status_code():
    porcelain = "\n".join([
        " M file1.py",
        " M file2.py",
        "?? new_file.py",
        "A  staged.py",
    ])
    out = cli.summarize_status(porcelain)
    # Sorted by status code — "?" sorts before letters in ASCII.
    assert out == "??:1, A:1, M:2"


def test_summarize_status_handles_empty_short_line():
    # Lines under 2 chars whose [:2].strip() is empty fall under "??".
    assert cli.summarize_status("\n") == "??:1"


# ---------- escape_md_inline ----------

def test_escape_md_inline_replaces_backticks():
    assert cli.escape_md_inline("fix `foo` bug") == "fix \\`foo\\` bug"


def test_escape_md_inline_leaves_plain_text():
    assert cli.escape_md_inline("normal message") == "normal message"


# ---------- dict_to_toml ----------

def test_dict_to_toml_strings_and_ints():
    out = cli.dict_to_toml({"name": "worknow", "max": 42})
    assert 'name = "worknow"' in out
    assert "max = 42" in out


def test_dict_to_toml_list_of_strings():
    out = cli.dict_to_toml({"roots": ["~/a", "~/b"]})
    assert 'roots = [' in out
    assert '"~/a"' in out
    assert '"~/b"' in out


def test_dict_to_toml_rejects_unsupported_types():
    with pytest.raises(TypeError):
        cli.dict_to_toml({"weird": 3.14})


def test_dict_to_toml_round_trips_default_config():
    import tomllib
    text = cli.dict_to_toml(cli.DEFAULT_CONFIG)
    parsed = tomllib.loads(text)
    assert parsed == cli.DEFAULT_CONFIG


# ---------- expand ----------

def test_expand_user_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = cli.expand("~/foo")
    assert str(result).startswith(str(tmp_path.resolve()))


def test_expand_envvar(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKNOW_TEST_PATH", str(tmp_path))
    result = cli.expand("$WORKNOW_TEST_PATH")
    assert result == tmp_path.resolve()


# ---------- find_git_repos ----------

def test_find_git_repos_picks_up_direct_children(tmp_path):
    (tmp_path / "repo_a" / ".git").mkdir(parents=True)
    (tmp_path / "repo_b" / ".git").mkdir(parents=True)
    (tmp_path / "not_a_repo").mkdir()
    (tmp_path / ".hidden" / ".git").mkdir(parents=True)  # hidden — skipped

    repos = cli.find_git_repos([str(tmp_path)], max_projects=10)
    names = sorted(r.name for r in repos)
    assert names == ["repo_a", "repo_b"]


def test_find_git_repos_respects_max_projects(tmp_path):
    for i in range(5):
        (tmp_path / f"repo_{i}" / ".git").mkdir(parents=True)
    repos = cli.find_git_repos([str(tmp_path)], max_projects=2)
    assert len(repos) == 2


def test_find_git_repos_skips_missing_roots():
    repos = cli.find_git_repos(["/nonexistent/path/12345"], max_projects=10)
    assert repos == []


def test_find_git_repos_deduplicates(tmp_path):
    (tmp_path / "repo" / ".git").mkdir(parents=True)
    repos = cli.find_git_repos([str(tmp_path), str(tmp_path)], max_projects=10)
    assert len(repos) == 1


# ---------- render ----------

def test_render_with_no_projects():
    out = cli.render(projects=[], processes=[], sessions_text="", config={})
    assert "# Current Work" in out
    assert "No active git projects" in out
    assert "No matching agent/build" in out


def test_render_includes_project_details():
    project = cli.GitProject(
        path=pathlib.Path("/tmp/foo"),
        branch="feature/x",
        dirty=True,
        changes="M:2, ??:1",
        last_commit="2 hours ago · fix bug",
        recent_commits=["1 day ago · commit a", "2 days ago · commit b"],
    )
    out = cli.render([project], [], "", {})
    assert "### foo" in out
    assert "feature/x" in out
    assert "M:2, ??:1" in out
    assert "fix bug" in out
    assert "commit a" in out


def test_render_escapes_backticks_in_commits():
    project = cli.GitProject(
        path=pathlib.Path("/tmp/foo"),
        branch="main",
        dirty=False,
        changes="clean",
        last_commit="now · refactor `parseFoo` helper",
        recent_commits=[],
    )
    out = cli.render([project], [], "", {})
    assert "refactor \\`parseFoo\\` helper" in out
    assert "`parseFoo`" not in out  # raw backticks must be escaped


def test_render_sorts_dirty_first():
    clean = cli.GitProject(
        path=pathlib.Path("/tmp/aaa"),
        branch="topic",
        dirty=False,
        changes="clean",
        last_commit="x",
        recent_commits=["recent"],
    )
    dirty = cli.GitProject(
        path=pathlib.Path("/tmp/zzz"),
        branch="topic",
        dirty=True,
        changes="M:1",
        last_commit="y",
        recent_commits=[],
    )
    out = cli.render([clean, dirty], [], "", {})
    # dirty (zzz) appears before clean (aaa) despite alphabetical reversal.
    assert out.index("### zzz") < out.index("### aaa")


def test_render_includes_processes_with_cwd():
    proc = cli.ProcessInfo(pid="123", command="claude foo", cwd="/tmp/work")
    out = cli.render([], [proc], "", {})
    assert "`123`" in out
    assert "cwd: `/tmp/work`" in out
    assert "claude foo" in out


def test_render_truncates_processes_to_40():
    procs = [cli.ProcessInfo(pid=str(i), command=f"cmd{i}") for i in range(60)]
    out = cli.render([], procs, "", {})
    assert "cmd0" in out
    assert "cmd39" in out
    assert "cmd40" not in out


# ---------- batch_process_cwd parsing ----------

def test_batch_process_cwd_empty_list():
    assert cli.batch_process_cwd([]) == {}


# ---------- inspect_git_repos parallel wrapper ----------

def test_inspect_git_repos_returns_empty_for_no_paths():
    assert cli.inspect_git_repos([], recent_days=7) == []


# ---------- attach_agent_presence ----------

def test_attach_agent_presence_marks_repo_with_matching_cwd():
    project = cli.GitProject(
        path=pathlib.Path("/tmp/foo"),
        branch="main", dirty=False, changes="clean",
        last_commit="x", recent_commits=[],
    )
    proc = cli.ProcessInfo(pid="1", command="claude", cwd="/tmp/foo/src")
    cli.attach_agent_presence([project], [proc])
    assert project.has_active_agent is True


def test_attach_agent_presence_ignores_unrelated_cwds():
    project = cli.GitProject(
        path=pathlib.Path("/tmp/foo"),
        branch="main", dirty=False, changes="clean",
        last_commit="x", recent_commits=[],
    )
    proc = cli.ProcessInfo(pid="1", command="claude", cwd="/tmp/bar")
    cli.attach_agent_presence([project], [proc])
    assert project.has_active_agent is False


def test_attach_agent_presence_substring_doesnt_falsely_match():
    # cwd /tmp/foobar should NOT match project /tmp/foo — only equal paths
    # or path + separator count.
    project = cli.GitProject(
        path=pathlib.Path("/tmp/foo"),
        branch="main", dirty=False, changes="clean",
        last_commit="x", recent_commits=[],
    )
    proc = cli.ProcessInfo(pid="1", command="claude", cwd="/tmp/foobar")
    cli.attach_agent_presence([project], [proc])
    assert project.has_active_agent is False


# ---------- GitProject.is_active ----------

def test_is_active_when_dirty():
    p = cli.GitProject(
        path=pathlib.Path("/tmp/x"), branch="main", dirty=True,
        changes="M:1", last_commit="x", recent_commits=[],
    )
    assert p.is_active is True


def test_is_active_when_agent_present():
    p = cli.GitProject(
        path=pathlib.Path("/tmp/x"), branch="main", dirty=False,
        changes="clean", last_commit="x", recent_commits=[],
        has_active_agent=True,
    )
    assert p.is_active is True


def test_not_is_active_when_clean_and_no_agent():
    p = cli.GitProject(
        path=pathlib.Path("/tmp/x"), branch="main", dirty=False,
        changes="clean", last_commit="x", recent_commits=[],
    )
    assert p.is_active is False


# ---------- render_json ----------

def test_render_json_includes_active_count():
    import json as _json
    projects = [
        cli.GitProject(
            path=pathlib.Path("/tmp/a"), branch="main", dirty=True,
            changes="M:1", last_commit="x", recent_commits=[],
        ),
        cli.GitProject(
            path=pathlib.Path("/tmp/b"), branch="topic", dirty=False,
            changes="clean", last_commit="y", recent_commits=["recent"],
            has_active_agent=True,
        ),
        cli.GitProject(
            path=pathlib.Path("/tmp/c"), branch="main", dirty=False,
            changes="clean", last_commit="z", recent_commits=[],
        ),
    ]
    payload = _json.loads(cli.render_json(projects, [], ""))
    assert payload["schema_version"] == 1
    # Active: /tmp/a (dirty), /tmp/b (agent). Not /tmp/c.
    assert payload["active_tasks_count"] == 2
    assert len(payload["repos"]) == 3
    # Active repos sorted to the top.
    assert payload["repos"][0]["is_active"] is True
    assert payload["repos"][-1]["is_active"] is False


def test_render_json_truncates_processes_to_40():
    import json as _json
    procs = [cli.ProcessInfo(pid=str(i), command=f"cmd{i}") for i in range(60)]
    payload = _json.loads(cli.render_json([], procs, ""))
    assert len(payload["processes"]) == 40
