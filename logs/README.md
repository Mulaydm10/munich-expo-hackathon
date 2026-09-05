> **LOCKED governing file.** Do not edit in place. See `GOVERNANCE.md`.

# logs/ — raw experiment output

Convention: `logs/<EXP-####>_<YYYY-MM-DD>_<slug>/` — one directory per experiment run, named with
its `EXP-####` ID so `grep -rn 'EXP-####' .` recovers the full trace including raw output.

This directory's contents are gitignored by default (`logs/*` in the top-level `.gitignore`) except
for this `README.md` and `logs/.gitignore` themselves, which stay tracked. Raw logs are usually
large/regenerable and don't belong in git history; if a specific log is worth preserving long-term,
copy the relevant summary into `experiments/experiment_log.md` rather than un-ignoring the raw dir.
