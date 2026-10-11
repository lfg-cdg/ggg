import base64
import json

import pytest

from misis_lms import cli
from misis_lms.client import (Moodle, MoodleError, flatten, html_to_text, submission_types, text_to_html,
                              token_from_launch_url)

SITE = "https://lms.example"


class Resp:
    def __init__(self, data, content_type="application/json"):
        self.data, self.headers = data, {"Content-Type": content_type}

    def raise_for_status(self):
        pass

    def json(self):
        return self.data

    def iter_content(self, n):
        yield self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


class FakeHTTP:
    """Answers like a Moodle site; `functions` maps wsfunction -> response or callable(data)."""

    def __init__(self, functions=None):
        self.functions = functions or {}
        self.headers = {}
        self.calls = []

    def post(self, url, data=None, files=None, timeout=None):
        self.calls.append((url, data, [f[1][0] for f in files or []]))
        if url.endswith("/login/token.php"):
            if data["password"] == "right":
                return Resp({"token": "T0K"})
            return Resp({"error": "Неверный логин или пароль", "errorcode": "invalidlogin"})
        if url.endswith("/webservice/upload.php"):
            return Resp([{"itemid": 777, "filename": n} for n in self.calls[-1][2]])
        fn = self.functions[data["wsfunction"]]
        return Resp(fn(data) if callable(fn) else fn)

    def get(self, url, stream=False, timeout=None):
        self.calls.append((url, None, []))
        return Resp(b"PDFDATA", "application/pdf")


def assign(id, name, due=0, cutoff=0, plugins=("file",), **kw):
    configs = [{"plugin": p, "subtype": "assignsubmission", "name": "enabled", "value": "1"} for p in plugins]
    return {"id": id, "cmid": id + 1000, "name": name, "duedate": due, "cutoffdate": cutoff, "configs": configs,
            "intro": "<p>Do <b>it</b></p>", "introattachments": [], **kw}


def status(s):
    return {"lastattempt": {"submission": {"status": s}, "gradingstatus": "notgraded"}}


def test_flatten():
    assert flatten({"courseids": [5, 7], "plugindata": {"onlinetext_editor": {"text": "x", "format": 1}},
                    "flag": True, "skip": None}) == {
        "courseids[0]": 5, "courseids[1]": 7, "plugindata[onlinetext_editor][text]": "x",
        "plugindata[onlinetext_editor][format]": 1, "flag": 1}


def test_token_from_launch_url():
    raw = base64.b64encode(b"sig:::abc123:::private").decode().rstrip("=")
    assert token_from_launch_url(f"moodlemobile://token={raw}") == "abc123"


def test_html_and_text():
    assert html_to_text("<p>Hello&nbsp;<b>world</b></p><ul><li>a</li><li>b</li></ul>") == "Hello world\n- a\n- b"
    assert text_to_html("line 1\nline <2>\n\npara 2") == "<p>line 1<br>line &lt;2&gt;</p><p>para 2</p>"


def test_submission_types_ignore_disabled_and_comments():
    a = assign(1, "A", plugins=("file", "onlinetext", "comments"))
    a["configs"].append({"plugin": "onlinetext", "subtype": "assignsubmission", "name": "enabled", "value": "0"})
    assert submission_types(a)[:2] == ["file", "onlinetext"] and "comments" not in submission_types(a)


def test_login_ok_and_wrong_password():
    m = Moodle.login(SITE, "u", "right", session=FakeHTTP())
    assert m.token == "T0K"
    with pytest.raises(MoodleError) as e:
        Moodle.login(SITE, "u", "wrong", session=FakeHTTP())
    assert e.value.code == "invalidlogin"


def test_call_raises_on_exception():
    m = Moodle(SITE, "T", FakeHTTP({"x": {"exception": "moodle_exception", "errorcode": "invalidtoken",
                                          "message": "Invalid token"}}))
    with pytest.raises(MoodleError) as e:
        m.call("x")
    assert e.value.code == "invalidtoken"


def test_save_submission_uploads_then_saves(tmp_path):
    f = tmp_path / "report.pdf"
    f.write_bytes(b"x")
    http = FakeHTTP({"mod_assign_save_submission": []})
    assert Moodle(SITE, "T", http).save_submission(9, [str(f)], "my answer") == []
    upload, save = http.calls
    assert upload[1]["token"] == "T" and upload[2] == ["report.pdf"]
    assert save[1]["assignmentid"] == 9 and save[1]["plugindata[files_filemanager]"] == 777
    assert save[1]["plugindata[onlinetext_editor][text]"] == "<p>my answer</p>"


def test_file_url_adds_token_and_webservice_path():
    m = Moodle(SITE, "T", FakeHTTP())
    assert m.file_url(f"{SITE}/pluginfile.php/1/a.pdf") == f"{SITE}/webservice/pluginfile.php/1/a.pdf?token=T"
    assert m.file_url(f"{SITE}/webservice/pluginfile.php/1/a.pdf?forcedownload=1").endswith("?forcedownload=1&token=T")


@pytest.fixture
def site(monkeypatch, tmp_path):
    http = FakeHTTP({
        "core_webservice_get_site_info": {"userid": 42, "fullname": "Student"},
        "mod_assign_get_assignments": {"courses": [{"id": 3, "fullname": "Physics", "assignments": [
            assign(10, "Lab 1", due=2_000_000_000),
            assign(11, "Lab 0", due=1_000),
            assign(12, "Old", due=1_000, cutoff=1_000),
            assign(13, "Essay", due=1_900_000_000, plugins=("onlinetext",)),
        ]}]},
        "mod_assign_get_submission_status": lambda d: status({10: "new", 11: "draft", 12: "new",
                                                              13: "submitted"}[d["assignid"]]),
        "mod_assign_save_submission": [],
        "mod_assign_submit_for_grading": [],
    })
    monkeypatch.setattr(cli, "_client", lambda: Moodle(SITE, "T", http))
    monkeypatch.chdir(tmp_path)
    return http


def test_assignments_hides_submitted_and_closed(site, capsys):
    cli.main(["assignments", "--json"])
    rows = json.loads(capsys.readouterr().out)
    assert [r["id"] for r in rows] == [11, 10]
    assert rows[1]["url"] == f"{SITE}/mod/assign/view.php?id=1010" and rows[1]["accepts"] == ["file"]
    cli.main(["assignments", "--all", "--json"])
    assert len(json.loads(capsys.readouterr().out)) == 4


def test_show_json(site, capsys):
    cli.main(["show", "13", "--json"])
    s = json.loads(capsys.readouterr().out)
    assert s["description"] == "Do it" and s["status"] == "submitted" and s["accepts"] == ["onlinetext"]


def test_submit_draft_only_does_not_send(site, tmp_path, capsys):
    (tmp_path / "a.txt").write_text("x")
    cli.main(["submit", "10", str(tmp_path / "a.txt")])
    sent = [c[1]["wsfunction"] for c in site.calls if c[1] and "wsfunction" in c[1]]
    assert sent == ["mod_assign_save_submission"]
    assert "Not sent for grading" in capsys.readouterr().out


def test_submit_final_asks_and_can_cancel(site, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda _: "n")
    cli.main(["submit", "13", "--text", "answer", "--final"])
    sent = [c[1]["wsfunction"] for c in site.calls if c[1] and "wsfunction" in c[1]]
    assert "mod_assign_submit_for_grading" not in sent and "Cancelled" in capsys.readouterr().out


def test_submit_final_yes(site):
    cli.main(["submit", "13", "--text", "answer", "--final", "--yes"])
    sent = [c[1]["wsfunction"] for c in site.calls if c[1] and "wsfunction" in c[1]]
    assert sent[:2] == ["mod_assign_save_submission", "mod_assign_submit_for_grading"]


def test_login_saves_token_privately(monkeypatch, tmp_path):
    path = tmp_path / "cfg" / "config.json"
    monkeypatch.setattr(cli, "CONFIG_PATH", str(path))
    monkeypatch.setattr(cli, "Moodle", type("M", (Moodle,), {"site_info": lambda self: {"userid": 1, "fullname": "S"}}))
    cli.main(["login", "--site", SITE, "--token", "abc"])
    assert json.loads(path.read_text()) == {"site": SITE, "token": "abc"}
    assert path.stat().st_mode & 0o777 == 0o600


def test_text_output_and_download(site, capsys):
    site.functions["mod_assign_get_assignments"]["courses"][0]["assignments"][0]["introattachments"] = [
        {"filename": "task.pdf", "filesize": 7, "fileurl": f"{SITE}/webservice/pluginfile.php/1/task.pdf"}]
    cli.main(["assignments"])
    cli.main(["show", "10"])
    out = capsys.readouterr().out
    assert "Physics / Lab 1" in out and "task.pdf" in out and "Do it" in out
    cli.main(["download", "10"])
    assert open("lms_files/10 Lab 1/task.pdf", "rb").read() == b"PDFDATA"
    assert site.calls[-1][0].endswith("task.pdf?token=T")
