"""What a change to the readers or scorers does to real events, before it ships.

A test pins the cases someone thought of; this shows every case there is.
It builds two copies of the code -- a git ref (HEAD by default) and the
working tree -- and runs both over your own data, then prints each event the
change touches:

  mail     every email in the maildrop read again into a fresh database:
           events gained, lost, retitled, moved
  rescore  every stored event's text scored again: food verdict, networking
           kind and firms
  upgrade  a copy of your event log refreshed by each version (the mail part;
           no network): what the page would list afterwards. A change to how
           a title is read lands here as a second listing beside the old one,
           which a fresh database never shows.

Nothing is written to data/: the runs use scratch databases and read the
event log read-only. Run it before committing a change to the mail reader,
the newsletter splitter, the title check, the date reader or the scorers.

    python3 tools/compare_ingest.py            HEAD against the working tree
    python3 tools/compare_ingest.py c568875    an older commit instead
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Run inside each copy of the code. Prints one JSON object.
MAIL = r"""
import json, sys, store, ingest.mail as mail
conn = store.connect()
mail.run(conn)
out = {}
for r in conn.execute("SELECT * FROM events"):
    key = "%s|%s" % (r["source_id"] or r["id"], (r["starts_at"] or "")[:10])
    out[key] = {"title": r["title"], "starts_at": r["starts_at"], "ends_at": r["ends_at"],
                "location": r["location"], "tier": r["tier"],
                "network_kind": r["network_kind"]}
print(json.dumps(out))
"""

RESCORE = r"""
import json, sqlite3, sys
from score import score_event
from network import score_network
src = sqlite3.connect("file:%s?mode=ro" % sys.argv[1], uri=True)
src.row_factory = sqlite3.Row
out = {}
for r in src.execute("SELECT id, title, description, location, org FROM events "
                     "WHERE NOT COALESCE(dismissed, 0)"):
    food = score_event(title=r["title"] or "", description=r["description"] or "",
                       location=r["location"] or "")
    net = score_network(title=r["title"] or "", description=r["description"] or "",
                        location=r["location"] or "", org=r["org"] or "")
    out[r["id"]] = {"title": r["title"], "tier": food["tier"],
                    "network_kind": net["network_kind"],
                    "firms": sorted(f["name"] if isinstance(f, dict) else str(f)
                                    for f in net.get("firms") or [])}
print(json.dumps(out))
"""


UPGRADE = r"""
import json, sys
import docket
# The calendar and org pages come over the network; leave them as they are.
docket.livewhale.run = lambda conn, days=30: {"source": "livewhale", "skipped": True}
docket.orgs.run = lambda conn, cfg=None: {"source": "orgs", "skipped": True}
docket.refresh()
import store
conn = store.connect()
out = {}
for e in docket.build_events(conn, days=40):
    out[e["id"]] = {"title": e["title"], "starts_at": e["starts_at"], "tier": e.get("tier"),
                    "network_kind": e.get("network_kind"),
                    "firms": sorted(f["name"] for f in e.get("firms") or [])}
print(json.dumps(out))
"""


def export(ref: str, dest: str) -> None:
    archive = subprocess.run(["git", "-C", ROOT, "archive", ref], check=True,
                             capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", dest], input=archive, check=True)


def working_tree(dest: str) -> None:
    # New files too, or a change that adds a module fails to import.
    names = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--cached", "--others",
                            "--exclude-standard"], check=True,
                           capture_output=True).stdout.decode().split("\0")
    for name in filter(None, names):
        src = os.path.join(ROOT, name)
        if os.path.isfile(src):
            os.makedirs(os.path.dirname(os.path.join(dest, name)), exist_ok=True)
            shutil.copy2(src, os.path.join(dest, name))


def run(tree: str, program: str, scratch: str, *args) -> dict:
    with open(os.path.join(ROOT, "data", "config.json")) as fh:
        cfg = json.load(fh)
    cfg["mail"] = {"maildrop": os.path.join(ROOT, "data", "maildrop")}
    cfg.pop("imap", None)
    config = os.path.join(scratch, "config.json")
    with open(config, "w") as fh:
        json.dump(cfg, fh)
    env = dict(os.environ, FFF_DB=os.path.join(scratch, "events.db"),
               FFF_CONFIG=config, PYTHONDONTWRITEBYTECODE="1")
    out = subprocess.run([sys.executable, "-c", program, *args], cwd=tree, env=env,
                         capture_output=True, text=True)
    if out.returncode:
        sys.exit("%s failed:\n%s" % (tree, out.stderr[-2000:]))
    return json.loads(out.stdout.strip().splitlines()[-1])


def show(label: str, old: dict, new: dict, fields: tuple) -> int:
    lost = sorted(set(old) - set(new))
    gained = sorted(set(new) - set(old))
    changed = [k for k in sorted(set(old) & set(new))
               if any(old[k].get(f) != new[k].get(f) for f in fields)]
    print("\n== %s: %d events before, %d after; %d gained, %d lost, %d changed"
          % (label, len(old), len(new), len(gained), len(lost), len(changed)))
    for k in lost:
        print("  - %s  %s" % ((old[k].get("starts_at") or "")[:16], old[k]["title"][:80]))
    for k in gained:
        print("  + %s  %s" % ((new[k].get("starts_at") or "")[:16], new[k]["title"][:80]))
    for k in changed:
        print("  ~ %s" % (new[k]["title"] or "")[:80])
        for f in fields:
            if old[k].get(f) != new[k].get(f):
                print("      %s: %r -> %r" % (f, old[k].get(f), new[k].get(f)))
    return len(lost) + len(gained) + len(changed)


def main(argv: list) -> int:
    ref = argv[0] if argv else "HEAD"
    db = os.path.join(ROOT, "data", "events.db")
    with tempfile.TemporaryDirectory() as tmp:
        trees = {}
        for name in ("before", "after"):
            trees[name] = os.path.join(tmp, name)
            os.makedirs(trees[name])
        export(ref, trees["before"])
        working_tree(trees["after"])
        results = {}
        for name, tree in trees.items():
            for mode, program, args in (("mail", MAIL, ()), ("rescore", RESCORE, (db,)),
                                        ("upgrade", UPGRADE, ())):
                scratch = os.path.join(tmp, "%s-%s" % (name, mode))
                os.makedirs(scratch)
                if mode == "upgrade":
                    snapshot = sqlite3.connect(os.path.join(scratch, "events.db"))
                    with sqlite3.connect("file:%s?mode=ro" % db, uri=True) as live:
                        live.backup(snapshot)
                    snapshot.close()
                results[(name, mode)] = run(tree, program, scratch, *args)
        total = show("mail read again (%s -> working tree)" % ref,
                     results[("before", "mail")], results[("after", "mail")],
                     ("title", "starts_at", "ends_at", "location", "tier", "network_kind"))
        total += show("stored events scored again (%s -> working tree)" % ref,
                      results[("before", "rescore")], results[("after", "rescore")],
                      ("tier", "network_kind", "firms"))
        total += show("listed after a refresh of your event log (%s -> working tree)" % ref,
                      results[("before", "upgrade")], results[("after", "upgrade")],
                      ("title", "starts_at", "tier", "network_kind", "firms"))
    print("\n%d difference(s)" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
