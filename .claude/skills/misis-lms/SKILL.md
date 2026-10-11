---
name: misis-lms
description: Read assignments and submit work on newlms.misis.ru (Moodle LMS of NUST MISIS) for the user. Use when the user asks about their assignments, deadlines, grades, task files, or wants to hand in work.
---

# MISIS LMS (newlms.misis.ru)

All work goes through the CLI in this repo: `python -m misis_lms <command>`. Run it from the repo root.
It talks to the Moodle web service API with the user's own token (saved in `~/.config/misis_lms/config.json`).

## Commands

| Command | What it does |
|---|---|
| `assignments --json` | Open assignments: not submitted and not closed, sorted by due date. `--all` adds the rest, `--course ID` filters. |
| `show ID --json` | Full description, attached files, accepted submission types, file limits, current status and grade. |
| `download ID [--out DIR]` | Downloads the files attached to the assignment (default `lms_files/<id> <name>/`). |
| `courses --json` | The user's courses. |
| `submit ID FILE... [--text T / --text-file F]` | Uploads files / online text and saves them as a **draft**. Files replace the files already in the draft. |
| `submit ID --final --yes` | Sends the draft for grading. |

Dates in JSON are Unix timestamps. `status` is `new`, `draft`, `submitted` or `reopened`.

## Rules

1. **Login is the user's job.** Never ask for the password in chat and never put it in a command line.
   If a command says "Not logged in" or "token is not valid", ask the user to run
   `python -m misis_lms login` themselves in their terminal.
2. **Before every `--final`, ask the user in chat and wait for a clear "yes".** Show them: the assignment
   name, the files / text that will be sent, and say that after sending the work usually cannot be changed and that
   it accepts the site's statement that this is their own work. Only then run `submit ID --final --yes`.
   Saving a draft (without `--final`) is fine without asking, if the user asked to upload the work.
3. Before submitting, run `show ID --json` and check `accepts` (file / onlinetext), `max_files` and `file_types`.
4. After submitting, run `show ID --json` again and tell the user the new status.
5. Do not invent the student's work. Upload what the user gives you or what the user asks you to send.

## If login with a password does not work (SSO)

If the user signs in on the site through a single MISIS account, `login` with a password may fail with `invalidlogin`.
Then the user can open
`https://newlms.misis.ru/admin/tool/mobile/launch.php?service=moodle_mobile_app&passport=1&urlscheme=moodlemobile`
in a browser where they are logged in, copy the `moodlemobile://token=...` address the browser tries to open
(DevTools → Network or Console shows it), and run `python -m misis_lms login --token "moodlemobile://token=..."`.
