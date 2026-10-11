"""Client for the Moodle web service API (made for newlms.misis.ru, works with any Moodle site).

Uses the same service as the Moodle mobile app (moodle_mobile_app), so a normal student
account is enough: no admin has to create a special token.
"""
import base64
import html
import os
import re

import requests

DEFAULT_SITE = "https://newlms.misis.ru"
SERVICE = "moodle_mobile_app"


class MoodleError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(f"{message} ({code})")
        self.code = code


def flatten(params: dict) -> dict:
    """Moodle wants PHP-style form keys: {'a': [1, {'b': 2}]} -> {'a[0]': 1, 'a[1][b]': 2}."""
    out = {}

    def walk(prefix, value):
        if isinstance(value, dict):
            for k, v in value.items():
                walk(f"{prefix}[{k}]" if prefix else k, v)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(f"{prefix}[{i}]", v)
        elif isinstance(value, bool):
            out[prefix] = int(value)
        elif value is not None:
            out[prefix] = value

    walk("", params)
    return out


def token_from_launch_url(url: str) -> str:
    """Decode 'moodlemobile://token=<base64>' (the SSO login flow of the mobile app).

    The base64 part is 'signature:::token' or 'signature:::token:::privatetoken'.
    """
    data = url.split("token=", 1)[1].strip().rstrip("/")
    data += "=" * (-len(data) % 4)
    parts = base64.b64decode(data).decode().split(":::")
    if len(parts) < 2:
        raise ValueError("unexpected token format in the launch URL")
    return parts[1]


def html_to_text(s: str) -> str:
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", s or "")
    s = re.sub(r"(?i)<li[^>]*>", "- ", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    s = re.sub(r"[ \t\xa0]+", " ", s)
    return re.sub(r"\n\s*\n+", "\n\n", s).strip()


def text_to_html(s: str) -> str:
    paras = [p for p in re.split(r"\n\s*\n", s.strip()) if p.strip()]
    return "".join("<p>" + html.escape(p).replace("\n", "<br>") + "</p>" for p in paras)


class Moodle:
    def __init__(self, site: str = DEFAULT_SITE, token: str | None = None,
                 session: requests.Session | None = None):
        self.site = site.rstrip("/")
        self.token = token
        self.http = session or requests.Session()
        self.http.headers.setdefault("User-Agent", "misis-lms-agent/0.1")
        self._userid = None

    @classmethod
    def login(cls, site: str, username: str, password: str,
              session: requests.Session | None = None) -> "Moodle":
        m = cls(site, session=session)
        r = m.http.post(f"{m.site}/login/token.php",
                        data={"username": username, "password": password, "service": SERVICE}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if "token" not in data:
            raise MoodleError(data.get("errorcode", "unknown"), data.get("error", str(data)))
        m.token = data["token"]
        return m

    def call(self, function: str, **params):
        r = self.http.post(f"{self.site}/webservice/rest/server.php",
                           data={"wstoken": self.token, "wsfunction": function,
                                 "moodlewsrestformat": "json", **flatten(params)}, timeout=60)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "exception" in data:
            raise MoodleError(data.get("errorcode", "unknown"), data.get("message", str(data)))
        return data

    # --- reading -------------------------------------------------------------------------

    def site_info(self) -> dict:
        info = self.call("core_webservice_get_site_info")
        self._userid = info["userid"]
        return info

    @property
    def userid(self) -> int:
        if self._userid is None:
            self.site_info()
        return self._userid

    def courses(self) -> list[dict]:
        return self.call("core_enrol_get_users_courses", userid=self.userid)

    def assignments(self, course_ids: list[int] | None = None) -> list[dict]:
        """All assignments of the given courses (all my courses if None), each with 'course' info added."""
        data = self.call("mod_assign_get_assignments", courseids=course_ids or [])
        out = []
        for c in data.get("courses", []):
            for a in c.get("assignments", []):
                out.append({**a, "course_id": c["id"], "course_name": c.get("fullname", "")})
        return out

    def submission_status(self, assign_id: int) -> dict:
        return self.call("mod_assign_get_submission_status", assignid=assign_id)

    def file_url(self, url: str) -> str:
        """Files from the API need the token; /pluginfile.php only works with a browser session."""
        if "/webservice/pluginfile.php" not in url:
            url = url.replace("/pluginfile.php", "/webservice/pluginfile.php", 1)
        return url + ("&" if "?" in url else "?") + "token=" + self.token

    def download(self, url: str, path: str) -> str:
        with self.http.get(self.file_url(url), stream=True, timeout=120) as r:
            r.raise_for_status()
            if r.headers.get("Content-Type", "").startswith("application/json"):
                err = r.json()
                raise MoodleError(err.get("errorcode", "unknown"), err.get("error", str(err)))
            tmp = path + ".part"
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 16):
                    f.write(chunk)
            os.replace(tmp, path)
        return path

    # --- writing -------------------------------------------------------------------------

    def upload(self, paths: list[str]) -> int:
        """Upload files to a new draft area and return its item id."""
        files = []
        try:
            for i, p in enumerate(paths, 1):
                files.append((f"file_{i}", (os.path.basename(p), open(p, "rb"))))
            r = self.http.post(f"{self.site}/webservice/upload.php",
                               data={"token": self.token, "filearea": "draft", "itemid": 0},
                               files=files, timeout=300)
        finally:
            for _, (_, fh) in files:
                fh.close()
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            raise MoodleError(data.get("errorcode", "unknown"), data.get("error", str(data)))
        return data[0]["itemid"]

    def save_submission(self, assign_id: int, files: list[str] | None = None,
                        text: str | None = None) -> list[dict]:
        """Save (or replace) my draft. Files given here replace the files already in the draft.

        Returns Moodle warnings; an empty list means success.
        """
        plugindata = {}
        if files:
            plugindata["files_filemanager"] = self.upload(files)
        if text is not None:
            plugindata["onlinetext_editor"] = {"text": text_to_html(text), "format": 1, "itemid": 0}
        if not plugindata:
            raise ValueError("nothing to submit: give files and/or text")
        return self.call("mod_assign_save_submission", assignmentid=assign_id, plugindata=plugindata)

    def submit_for_grading(self, assign_id: int) -> list[dict]:
        """Final submit. Accepts the site's submission statement ("this is my own work") if it has one."""
        return self.call("mod_assign_submit_for_grading", assignmentid=assign_id,
                         acceptsubmissionstatement=1)


def config(a: dict, name: str, plugin: str | None = None, subtype: str | None = None) -> str | None:
    """Read one setting from assignment['configs']."""
    for c in a.get("configs", []):
        if c.get("name") == name and plugin in (None, c.get("plugin")) and subtype in (None, c.get("subtype")):
            return c.get("value")
    return None


def submission_types(a: dict) -> list[str]:
    """['file', 'onlinetext'] - what this assignment accepts."""
    return [c["plugin"] for c in a.get("configs", [])
            if c.get("subtype") == "assignsubmission" and c.get("name") == "enabled"
            and str(c.get("value")) == "1" and c.get("plugin") != "comments"]


def my_submission(status: dict) -> dict:
    last = status.get("lastattempt") or {}
    return last.get("submission") or last.get("teamsubmission") or {}
