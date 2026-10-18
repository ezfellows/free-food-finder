"""Is this addressed to one reader, or to the school?

The last line before anything reaches a public page. Every other check asks
whether a *sender* may be read; this one asks whether a particular message was
meant for this mailbox alone, and it runs even on senders that are trusted --
an org address still carries "your callback interview with Covington", and a
calendar invite from a classmate is still an invitation to one person.

The distinction that makes it workable is **bulk**. CampusGroups greets every
member by name, so "Dear Alex" proves nothing on its own; a message addressed
to the whole student body is public however personally it is written. So the
personal signals below only fire when nothing marks the message as bulk.

The same rule settles the harder case. "Invitation only", "members only" and
"1Ls only" describe restricted events that are still announced to everybody,
and `details.py` already publishes them as tags a reader can act on. Blocking
on that wording would delete real events. Blocking on that wording *in a
message that went to one person* is the difference between "the ADR Bootcamp
is for members" and "you are invited to dinner with the Dean".

Nothing here needs to know whose mailbox it is. A greeting is personal when it
names one individual in a message with no bulk markers; which name is not the
point, and not storing it is one less thing on disk.
"""

from __future__ import annotations

import email.utils
import re

# Somebody's calendar sent this, and a calendar invites people, not lists.
INVITE_SUBJECT = re.compile(
    r"^\s*(?:invitation|invite|accepted|declined|tentative|updated\s+invitation|"
    r"cancelled\s+event|canceled\s+event)\s*:", re.I)

# Written to a reader about that reader.
# Note what is NOT here: a bare "you are invited". Half the student orgs open
# with "You're invited to our first GBM!" and blocking that empties the feed.
# An invitation only counts as personal when it is qualified as one -- it was
# extended to this reader in particular, or to a private gathering.
SECOND_PERSON = re.compile(
    r"\byou\s+(?:have\s+been|are|were)\s+(?:selected|chosen|accepted|"
    r"nominated|shortlisted|approved|matched|assigned|admitted)\b|"
    r"\bpersonally\s+invited\b|\byou\s+specifically\b|"
    r"\bfor\s+your\s+eyes\s+only\b|"
    r"\byou\s+(?:are|have\s+been)\s+(?:cordially\s+)?invited\s+to\s+"
    r"(?:a\s+|an\s+|our\s+)?(?:private|exclusive|invitation-?only|"
    r"invite-?only|closed)\b|"
    r"\bwe(?:'re| are)\s+(?:pleased|delighted)\s+to\s+offer\s+you\b|"
    r"\bcongratulations[,!]?\s+you\b|"
    # Narrow on purpose. "Reserve your spot", "save your seat" and "we are
    # delighted to invite you" are how public events are advertised, and an
    # earlier, wider version of this flagged five real broadcast events --
    # an alumni reception, a research colloquium, a panel on homelessness.
    # What is left names something only one person can have.
    r"\byour\s+(?:interview|callback|call\s+back|appointment|candidacy|"
    r"offer\s+letter|one-on-one|coffee\s+chat)\b|"
    r"\byour\s+(?:meeting|call)\s+with\s+[A-Z]|"
    # Forward-looking: it confirms a place this reader now holds. Retrospective
    # thanks ("for attending") is a different thing and lives in COURTESY.
    r"\bthank\s+you\s+for\s+(?:registering|joining)\b|"
    # Written to the people who already signed up. "If you are receiving this
    # email, you have registered for Lunch with Dean Moreno" went to eleven
    # named 1Ls and nobody else -- a closed event with a room number on it.
    # It reached this line marked BULK, because "you are receiving this" is
    # also how a newsletter opens: the very sentence proving the event was
    # private was read as proof it was public. These sit in SECOND_PERSON,
    # which fires whether or not anything marks the message as bulk.
    r"\byou\s+(?:have\s+)?registered\s+for\b|"
    r"\byou\s+(?:are|have\s+been)\s+registered\s+for\b|"
    r"\byou\s+(?:are\s+)?confirmed\s+for\b|"
    r"\byour\s+registration\s+for\b|"
    r"\byou\s+signed\s+up\s+for\b|"
    r"\byou\s+are\s+signed\s+up\s+to\s+join\b|"
    r"\bwe\s+(?:enjoyed|loved)\s+meeting\s+you\b|"
    r"\bwe\s+(?:have\s+)?received\s+your\b",
    re.I)

# Advice about candidacies in general, not news about one. The Private Sector
# Pulse of 8 October 2026 went to every 1L and was refused whole for "the
# interview can greatly impact your candidacy". A recruiter writing about
# yours states a fact ("we are moving forward with your candidacy"); advice
# says what something can do to it. Only that phrase is excused.
ADVICE = re.compile(
    r"\b(?:can|could|may|might|will|would)\s+(?:\w+\s+){0,2}"
    r"(?:impact|affect|influence|strengthen|weaken|help|hurt|harm|boost|"
    r"improve)\s+your\s+candidacy\b", re.I)


def _second_person(text: str):
    """The first SECOND_PERSON match that is not general advice, or None."""
    advice = [(m.start(), m.end()) for m in ADVICE.finditer(text)]
    for hit in SECOND_PERSON.finditer(text):
        if not any(start <= hit.start() and hit.end() <= end
                   for start, end in advice):
            return hit
    return None

# A courtesy about something that already happened. Personal in a one-to-one
# note ("thank you for registering -- here is your confirmation"), and routine
# at the top of any org newsletter recapping last night's meeting. Held to the
# same bulk gate as EXCLUSIVE below: the Antitrust Association opened with
# "thank you for attending" and went on to announce a Ropes & Gray panel with
# three attorneys and food -- exactly what this site exists to list, and it
# was being thrown away on the strength of the greeting.
COURTESY = re.compile(
    r"\bthank\s+you\s+for\s+(?:attending|coming)\b", re.I)

# Exclusivity, which only counts when the message is not bulk. On a broadcast
# these are ordinary access notes that `details.py` turns into tags.
EXCLUSIVE = re.compile(
    r"\bby\s+invitation\s+only\b|\binvitation\s+only\b|\binvite-?\s?only\b|"
    r"\bexclusive(?:ly)?\s+(?:for|to|invited)\b|\bprivate\s+(?:event|dinner|"
    r"reception|gathering)\b|\bdo\s+not\s+(?:forward|share|distribute)\b|"
    r"\bplease\s+keep\s+this\s+confidential\b",
    re.I)

# "Dear Alex," -- one given name, at the very top of the message.
SALUTATION = re.compile(
    r"^\s{0,4}(?i:dear|hi|hello|hey|good\s+(?:morning|afternoon|evening))\s+"
    r"(?!(?:All|Everyone|Everybody|Team|Students|Folks|Friends|Colleagues|"
    r"There)\b)([A-Z][a-z]{1,19})\s*[,!.]",
    re.M)

# Anything that says the message went to a crowd. An unsubscribe link is the
# most reliable: nobody puts one on a note to a friend.
BULK_BODY = re.compile(
    r"\bunsubscribe\b|\bmanage\s+(?:your\s+)?(?:email\s+)?preferences\b|"
    r"\bview\s+this\s+email\s+in\s+your\s+browser\b|"
    r"\bemail\s+sent\s+by\b|\byou\s+are\s+receiving\s+this\b|"
    r"\bto\s+view\s+this\s+email\s+as\s+a\s+web\s+page\b",
    re.I)

BULK_HEADERS = ("List-Unsubscribe", "List-Id", "List-Post", "X-Campaign-Id",
                "X-Mailer-Campaign", "Precedence", "Auto-Submitted")


def _header_says_bulk(msg) -> bool:
    for name in BULK_HEADERS:
        value = (msg.get(name) or "").strip().lower()
        if not value:
            continue
        if name == "Precedence":
            if value in ("bulk", "list", "junk"):
                return True
        elif name == "Auto-Submitted":
            if value != "no":
                return True
        else:
            return True
    return False


def is_bulk(msg=None, body: str = "") -> bool:
    """Whether anything shows this message went to more than one reader."""
    if msg is not None and _header_says_bulk(msg):
        return True
    if msg is not None:
        # Imported late: `senders` imports this module for the fail-safe, so a
        # module-level import would be circular.
        import senders
        sender = senders.address_of(msg.get("From", ""))
        if senders.is_broadcast_domain(sender):
            return True
        if senders.to_a_listserv(msg):
            return True
        # Addressed to itself. A firm mailing every 1L in the city puts its own
        # recruiting mailbox in To and everyone else in Bcc -- "To:
        # Recruit@weil.com" from "Recruit@weil.com" is a blast, not a note.
        if sender and addressed_to_itself(msg, sender):
            return True
    return bool(BULK_BODY.search(body or ""))


def _sender_is_individual(msg) -> bool:
    """Whether the From address looks like a person rather than an office.

    The sole-recipient rule leans on this. A role account writing to one
    reader is a newsletter with a small list -- `lawapalsa@` mails each member
    their own copy -- while a netID or an outside address writing to one
    reader is a conversation. Without the distinction the rule blocks every
    org newsletter that happens to carry no unsubscribe footer.
    """
    import senders
    address = senders.address_of(msg.get("From", "")) if msg is not None else ""
    if not address or senders.is_broadcast_domain(address):
        return False
    if senders.looks_personal(address):
        return True
    return not senders.is_school(address)


def addressed_to_itself(msg, sender: str) -> bool:
    seen = set()
    for field in ("To", "Cc"):
        for value in msg.get_all(field) or []:
            for _, address in email.utils.getaddresses([value]):
                if address:
                    seen.add(address.lower())
    return bool(seen) and seen <= {sender.lower()}


def bcc_to_the_class(msg) -> bool:
    """A firm's mailing to the class, sent with the students in Bcc.

    What shows is nobody, or only the firm's own people: Akin's 1L programs of
    13 Oct 2026 went out with its legal-talent mailbox in Cc and the class
    hidden, and Davis Polk's 1L receptions with no visible recipient at all.
    Both were refused as a firm "writing to one reader". A firm's note to one
    student has that student in To. Read only for a firm's address: a school
    sender shares the student's own domain, so the same shape says nothing.

    It answers the question the address checks ask -- one reader, or many --
    and nothing else. What the message says is still read as before.
    """
    import senders
    sender = senders.address_of(msg.get("From", "")) if msg is not None else ""
    if not sender or not senders.firm_recruiting(sender):
        return False
    home = _base_domain(sender)
    for field in ("To", "Cc"):
        for value in msg.get_all(field) or []:
            for _, address in email.utils.getaddresses([value]):
                if address and "@" in address and _base_domain(address) != home:
                    return False
    return True


def _base_domain(address: str) -> str:
    """"us.akingump.com" and "akingump.com" are one firm."""
    return ".".join(address.lower().rpartition("@")[2].split(".")[-2:])


def recipient_count(msg) -> int:
    seen = set()
    for field in ("To", "Cc", "Bcc"):
        for value in msg.get_all(field) or []:
            for _, address in email.utils.getaddresses([value]):
                if address and "@" in address:
                    seen.add(address.lower())
    return len(seen)


def has_calendar_part(msg) -> bool:
    if not hasattr(msg, "walk"):
        return False
    return any(part.get_content_type() == "text/calendar" for part in msg.walk())


def assess_text(text: str, bulk: bool = False) -> dict:
    """Whether a passage is written to one reader. `{"personal", "reason"}`."""
    body = text or ""
    hit = _second_person(body)
    if hit:
        return {"personal": True,
                "reason": "written to one reader (%r)" % hit.group(0).strip()[:40]}
    if not bulk:
        hit = EXCLUSIVE.search(body)
        if hit:
            return {"personal": True,
                    "reason": "an exclusive invitation (%r)"
                              % hit.group(0).strip()[:40]}
        hit = COURTESY.search(body)
        if hit:
            return {"personal": True,
                    "reason": "written to one reader (%r)"
                              % hit.group(0).strip()[:40]}
    return {"personal": False, "reason": ""}


def assess(msg, body: str = "", allowlisted: bool = False) -> dict:
    """Whether this message was meant for this mailbox alone.

    Returns `{"personal": bool, "reason": str}`. The reason is written to be
    read in an audit -- it names the signal, so a wrong call can be argued
    with rather than guessed at.

    `allowlisted` suppresses **only** the sole-recipient rule, which is a
    guess about the shape of an address rather than evidence about the
    message: a student org mailing from a Gmail account writes to one reader
    at a time and looks exactly like a classmate. Saying "this address is an
    organisation" settles that. It settles nothing about a calendar invite, a
    callback interview or a private dinner, so those still block.
    """
    subject = (msg.get("Subject", "") or "") if msg is not None else ""

    if msg is not None and INVITE_SUBJECT.search(subject):
        return {"personal": True,
                "reason": "a calendar invitation addressed to you"}
    if msg is not None and has_calendar_part(msg):
        return {"personal": True, "reason": "carries a calendar invitation"}

    bulk = is_bulk(msg, body)
    verdict = assess_text(f"{subject}\n{body}", bulk)
    if verdict["personal"]:
        return verdict

    if bulk or msg is None:
        return {"personal": False, "reason": ""}

    greeting = SALUTATION.search(body or "")
    if greeting:
        return {"personal": True,
                "reason": "greets one person by name (%r) with nothing "
                          "marking it a broadcast" % greeting.group(1)}
    if (not allowlisted and recipient_count(msg) == 1
            and _sender_is_individual(msg) and not bcc_to_the_class(msg)):
        return {"personal": True,
                "reason": "one person writing to one reader, with nothing "
                          "marking it a broadcast"}
    return {"personal": False, "reason": ""}


def is_personal(msg, body: str = "", allowlisted: bool = False) -> bool:
    return assess(msg, body, allowlisted)["personal"]
