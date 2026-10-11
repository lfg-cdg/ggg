"""Command line: `python -m misis_lms login | courses | assignments | show | download | submit`.

Every reading command has --json, so an agent can parse the output.
"""
import argparse
import getpass
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .client import (DEFAULT_SITE, Moodle, MoodleError, config, html_to_text, my_submission,
                     submission_types, token_from_launch_url)

CONFIG_PATH = os.environ.get("MISIS_LMS_CONFIG", os.path.expanduser("~/.config/misis_lms/config.json"))


def _load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def _save_config(cfg: dict) -> None:
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f)


def _client() -> Moodle:
    cfg = _load_config()
    token = os.environ.get("MISIS_LMS_TOKEN") or cfg.get("token")
    if not token:
        sys.exit("Not logged in. Run: python -m misis_lms login")
    return Moodle(os.environ.get("MISIS_LMS_SITE") or cfg.get("site") or DEFAULT_SITE, token)


def _date(ts) -> str:
    return datetime.fromtimestamp(ts).strftime("%d.%m.%Y %H:%M") if ts else "-"


def _print_json(data) -> None:
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    print()


def _summary(m: Moodle, a: dict, st: dict) -> dict:
    sub = my_submission(st)
    last = st.get("lastattempt") or {}
    fb = st.get("feedback") or {}
    return {
        "id": a["id"],
        "cmid": a.get("cmid"),
        "name": a["name"],
        "course_id": a["course_id"],
        "course": a["course_name"],
        "due": a.get("duedate") or None,
        "cutoff": a.get("cutoffdate") or None,
        "status": sub.get("status", "new"),
        "grading_status": last.get("gradingstatus"),
        "grade": fb.get("gradefordisplay"),
        "can_edit": last.get("canedit"),
        "can_submit": last.get("cansubmit"),
        "accepts": submission_types(a),
        "url": f"{m.site}/mod/assign/view.php?id={a['cmid']}" if a.get("cmid") else None,
    }


# --- commands --------------------------------------------------------------------------------


def cmd_login(args) -> None:
    site = (args.site or os.environ.get("MISIS_LMS_SITE") or DEFAULT_SITE).rstrip("/")
    if args.token:
        token = token_from_launch_url(args.token) if "token=" in args.token else args.token.strip()
        m = Moodle(site, token)
    else:
        user = args.username or os.environ.get("MISIS_LMS_USERNAME") or input("Login: ")
        pwd = os.environ.get("MISIS_LMS_PASSWORD") or getpass.getpass("Password: ")
        m = Moodle.login(site, user, pwd)
    info = m.site_info()
    _save_config({"site": site, "token": m.token})
    print(f"Logged in as {info['fullname']} (user id {info['userid']}) on {info.get('sitename', site)}.")
    print(f"Token saved to {CONFIG_PATH} (only you can read this file).")


def cmd_courses(args) -> None:
    courses = _client().courses()
    if args.json:
        _print_json([{"id": c["id"], "name": c["fullname"], "short": c.get("shortname")} for c in courses])
        return
    for c in courses:
        print(f"{c['id']:>6}  {c['fullname']}")


def cmd_assignments(args) -> None:
    m = _client()
    items = m.assignments(args.course)
    with ThreadPoolExecutor(8) as ex:
        statuses = list(ex.map(lambda a: m.submission_status(a["id"]), items))
    now = time.time()
    rows = []
    for a, st in zip(items, statuses):
        s = _summary(m, a, st)
        closed = s["cutoff"] and s["cutoff"] < now
        if args.all or (s["status"] != "submitted" and not closed):
            rows.append(s)
    rows.sort(key=lambda s: (s["due"] or float("inf"), s["course"]))
    if args.json:
        _print_json(rows)
        return
    if not rows:
        print("No open assignments." if not args.all else "No assignments.")
        return
    print(f"{'ID':>7}  {'Due':<16} {'Status':<10} {'Grade':<10} Course / assignment")
    print("-" * 90)
    for s in rows:
        print(f"{s['id']:>7}  {_date(s['due']):<16} {s['status']:<10} {s['grade'] or '-':<10} "
              f"{s['course']} / {s['name']}")


def _find(m: Moodle, assign_id: int) -> dict:
    for a in m.assignments():
        if a["id"] == assign_id:
            return a
    sys.exit(f"Assignment {assign_id} not found in your courses.")


def cmd_show(args) -> None:
    m = _client()
    a = _find(m, args.id)
    st = m.submission_status(a["id"])
    s = _summary(m, a, st)
    s["description"] = html_to_text(a.get("intro", ""))
    s["activity"] = html_to_text(a.get("activity", ""))
    s["attachments"] = [{"name": f["filename"], "size": f.get("filesize"), "url": f["fileurl"]}
                        for f in a.get("introattachments", [])]
    s["max_files"] = config(a, "maxfilesubmissions", "file")
    s["file_types"] = config(a, "filetypeslist", "file") or None
    s["word_limit"] = config(a, "wordlimit", "onlinetext") if config(a, "wordlimitenabled", "onlinetext") == "1" else None
    s["submitted_files"] = [f["filename"] for p in my_submission(st).get("plugins", [])
                            for area in p.get("fileareas", []) for f in area.get("files", [])]
    s["needs_final_submit"] = bool(a.get("submissiondrafts"))
    if args.json:
        _print_json(s)
        return
    print(f"[{s['id']}] {s['name']}\nCourse: {s['course']}\nURL: {s['url']}")
    print(f"Due: {_date(s['due'])}   Cut-off: {_date(s['cutoff'])}")
    print(f"Status: {s['status']}   Grading: {s['grading_status']}   Grade: {s['grade'] or '-'}")
    print(f"Accepts: {', '.join(s['accepts']) or '-'}"
          + (f"   Max files: {s['max_files']}" if s["max_files"] else "")
          + (f"   File types: {s['file_types']}" if s["file_types"] else "")
          + (f"   Word limit: {s['word_limit']}" if s["word_limit"] else ""))
    if s["submitted_files"]:
        print(f"Files in my submission: {', '.join(s['submitted_files'])}")
    print("\n" + (s["description"] or "(no description)"))
    if s["activity"]:
        print("\n" + s["activity"])
    if s["attachments"]:
        print("\nAttachments (python -m misis_lms download " + str(s["id"]) + "):")
        for f in s["attachments"]:
            print(f"  {f['name']}  ({f['size'] or '?'} bytes)")


def cmd_download(args) -> None:
    m = _client()
    a = _find(m, args.id)
    files = a.get("introattachments", [])
    if not files:
        print("This assignment has no attached files.")
        return
    out = args.out or os.path.join("lms_files", re.sub(r'[\\/:*?"<>|]+', "_", f"{a['id']} {a['name']}")[:80])
    os.makedirs(out, exist_ok=True)
    for f in files:
        path = m.download(f["fileurl"], os.path.join(out, os.path.basename(f["filename"])))
        print(f"Saved {path}")


def cmd_submit(args) -> None:
    m = _client()
    text = args.text
    if args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read()
    for p in args.files:
        if not os.path.isfile(p):
            sys.exit(f"File not found: {p}")
    if args.files or text is not None:
        warnings = m.save_submission(args.id, args.files, text)
        if warnings:
            _print_json(warnings)
            sys.exit("Moodle did not save the submission (see warnings above).")
        print("Draft saved" + (f": {', '.join(os.path.basename(p) for p in args.files)}" if args.files else "") + ".")
    elif not args.final:
        sys.exit("Nothing to do: give files, --text / --text-file, or --final.")
    if not args.final:
        print("Not sent for grading yet. You can still change it. Add --final to send it.")
        return
    if not args.yes:
        ans = input("Send for grading? After this you usually cannot change the work, and you accept "
                    "the site's statement that this is your own work. [y/N] ")
        if ans.strip().lower() not in ("y", "yes", "д", "да"):
            print("Cancelled. The draft is kept.")
            return
    warnings = m.submit_for_grading(args.id)
    if warnings:
        _print_json(warnings)
        sys.exit("Moodle did not accept the final submission (see warnings above).")
    st = my_submission(m.submission_status(args.id))
    print(f"Sent for grading. Status now: {st.get('status', '?')}.")


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="misis_lms", description="Assignments on newlms.misis.ru (Moodle) from the terminal")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("login", help="get an API token and save it")
    p.add_argument("--site", help=f"Moodle URL (default {DEFAULT_SITE})")
    p.add_argument("--username")
    p.add_argument("--token", help="paste a token, or the moodlemobile://token=... URL from the SSO login")
    p.set_defaults(func=cmd_login)

    p = sub.add_parser("courses", help="my courses")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_courses)

    p = sub.add_parser("assignments", help="open assignments (not submitted, not closed)")
    p.add_argument("--course", type=int, action="append", help="only this course id (repeatable)")
    p.add_argument("--all", action="store_true", help="also submitted and closed ones")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_assignments)

    p = sub.add_parser("show", help="full text, files and status of one assignment")
    p.add_argument("id", type=int)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("download", help="download the files attached to an assignment")
    p.add_argument("id", type=int)
    p.add_argument("--out", help="folder (default lms_files/<id> <name>)")
    p.set_defaults(func=cmd_download)

    p = sub.add_parser("submit", help="save files/text as a draft; --final sends it for grading")
    p.add_argument("id", type=int)
    p.add_argument("files", nargs="*", help="files to attach (they replace the files already in the draft)")
    p.add_argument("--text", help="online text answer")
    p.add_argument("--text-file", help="read the online text answer from this file")
    p.add_argument("--final", action="store_true", help="send for grading (asks for confirmation)")
    p.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    p.set_defaults(func=cmd_submit)

    args = ap.parse_args(argv)
    try:
        args.func(args)
    except MoodleError as e:
        if e.code in ("invalidtoken", "accessexception"):
            sys.exit(f"{e}\nThe token is not valid any more. Run: python -m misis_lms login")
        sys.exit(f"Moodle error: {e}")
