"""Build a .eml from the fields a mail connector returns.

The connector hands back parsed fields, not raw MIME. The ingesters want a
message, and building one here keeps the ingest contract unchanged: whatever
fetches the mail, what lands on disk is an ordinary .eml that `from_maildrop`
already knows how to read, and that `senders.classify` can still vet on its
real From and To headers.

The exception is a message fetched in RAW format, which is the original MIME
byte for byte -- every header, the HTML part, the real Message-ID. That is the
only form an OSL Weekly Broadcast is any use in, since its flyers are images
referenced from the HTML. A RAW result is far too large to come back inline,
so the harness saves it to a file, and `--from-result` takes that file.

    python3 tools/make_eml.py < items.json           fields, as a JSON list
    python3 tools/make_eml.py --from-result FILE...  a saved connector result
"""
import base64
import json
import os
import sys
from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "data", "maildrop")


def build(item) -> str:
    msg = EmailMessage()
    msg["From"] = item["sender"]
    msg["To"] = ", ".join(item.get("to") or ["undisclosed-recipients:;"])
    # Cc and Bcc as well as To: a student-body list in any of them is how the
    # vetting knows a message went to everybody.
    if item.get("cc"):
        msg["Cc"] = ", ".join(item["cc"])
    if item.get("bcc"):
        msg["Bcc"] = ", ".join(item["bcc"])
    msg["Subject"] = item.get("subject", "")
    when = item.get("date")
    if when:
        msg["Date"] = format_datetime(
            datetime.fromisoformat(when.replace("Z", "+00:00")))
    msg["Message-ID"] = "<%s@gmail-connector>" % item["id"]
    # Bulk headers when the source has them. The vetting leans on these to
    # tell a newsletter from a note, and without them it falls back to markers
    # in the body -- which is why the body must be kept whole, unsubscribe
    # footer and all, rather than trimmed to the interesting part.
    for header in ("List-Unsubscribe", "List-Id", "List-Post", "Precedence",
                   "Auto-Submitted"):
        value = item.get(header) or item.get(header.lower().replace("-", "_"))
        if value:
            msg[header] = value
    msg.set_content(item.get("body", ""))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "%s.eml" % item["id"])
    with open(path, "wb") as fh:
        fh.write(msg.as_bytes())
    return path


def from_result(path: str) -> str:
    """Save one get_message result the harness wrote to a file.

    RAW is written exactly as Gmail holds it. Anything else is built from the
    fields, as a JSON list on stdin would be.
    """
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    mid = str(data["id"])
    raw = data.get("raw")
    if raw:
        body = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        os.makedirs(OUT, exist_ok=True)
        out = os.path.join(OUT, "gm-%s.eml" % mid)
        with open(out, "wb") as fh:
            fh.write(body)
        return out
    return build({"id": "gm-" + mid, "sender": data.get("sender", ""),
                  "to": data.get("toRecipients"), "cc": data.get("ccRecipients"),
                  "bcc": data.get("bccRecipients"),
                  "subject": data.get("subject", ""), "date": data.get("date"),
                  "body": data.get("plaintextBody") or ""})


if __name__ == "__main__":
    if sys.argv[1:2] == ["--from-result"]:
        for name in sys.argv[2:]:
            print(from_result(name))
        sys.exit(0)
    items = json.load(sys.stdin)
    for item in items if isinstance(items, list) else [items]:
        print(build(item))
