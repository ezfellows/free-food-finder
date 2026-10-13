"""Who is allowed to put an event on a page other people read.

The docket is shared. Everything on it came out of a mailbox that also holds
loan statements, grades, a classmate's outline, and a partner at Orrick
agreeing to a coffee. None of that belongs on a website, and none of it
announces itself as private -- "Loan Decrease Confirmation Required" and
"LSCA GBM Tomorrow!" arrive through the same pipe, from the same domain, to
the same single recipient.

So this module decides, per message, whether the sender is broadcasting to the
school or writing to a person. It is **default deny**: a sender has to be
recognisably an organisation before anything of theirs is read. Getting that
backwards -- publish unless it looks private -- fails in the direction where
the mistake is a real person's financial details on a public page.

Three things do the work, in order:

1. **The sender's address.** The school's profile says what a person's
   address looks like. At Georgetown every netID carries a digit -- `zzz65`,
   `zz2152` -- and role accounts never do: `lawsba`, `cimgmt`, `cflo`. One
   character class separates a classmate from a student org there, with no
   list to go stale. A school with no profile has no such rule, so every
   address is a person's until you add it with `./fff sources add`.
2. **The sending system.** Outbound broadcast platforms -- Georgetown's
   `info.georgetown.edu` and `campusgroups.georgetown.edu` -- send nothing
   personal, so even a netID there (the Dean's office mails as one) is still
   a broadcast.
3. **The content**, as a last line. Even a genuine org address can carry a
   Banner ID or a loan number, and that must never reach the page whatever the
   envelope says.

Recipient count is deliberately **not** a signal. CampusGroups sends each
member their own copy, addressed to them alone, and so does a classmate asking
for an outline -- they are indistinguishable by envelope shape.
"""

from __future__ import annotations

import email.utils
import json
import os
import re
from functools import lru_cache

import personal
import school
import sources

# A law firm writing to the whole 1L class is an organisation broadcasting,
# not a person writing to a reader -- but it arrives from an ordinary
# firm.com address, which every rule above refuses. Two things recognise it.
#
# The first is the address: `Recruit@weil.com`, `MilbankRecruiting@milbank.com`,
# `US-Recruiting@cgsh.com`, `dcattorneyrecruitment@friedfrank.com`. Nobody
# holds a recruiting mailbox for personal correspondence.
RECRUITING_LOCAL = re.compile(
    r"(?:^|[._-])(?:recruit\w*|attorneyrecruit\w*|hiring|campus|"
    r"oci|legalrecruit\w*|talent|careers?|events?)(?:$|[._-])|"
    r"^(?:recruit\w*|attorneyrecruit\w*|legalrecruit\w*)", re.I)

FIRMS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "data", "firms.json")


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


@lru_cache(maxsize=1)
def firm_domains() -> dict:
    """{flattened domain label: firm name} from the cached firm list.

    The second is the domain. `milbank.com`, `weil.com`, `friedfrank.com` all
    fall out of the names already on file once punctuation is dropped. The
    initialisms do not -- `cgsh.com` is Cleary Gottlieb and nothing here can
    know that -- and they are exactly what the sender register is for.
    """
    out = {}
    try:
        with open(FIRMS_FILE, encoding="utf-8") as fh:
            firms = json.load(fh)
    except (OSError, ValueError):
        return out
    for entry in firms:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        slug, name = entry[0], entry[1]
        out.setdefault(_flat(slug), name)
        out.setdefault(_flat(name), name)
        first = _flat(re.split(r"[ ,&]", name)[0])
        if len(first) >= 5:
            out.setdefault(first, name)
    return out


def law_firm(address: str) -> str:
    """The firm this address belongs to, or "" when not a recognised one."""
    domain = domain_of(address)
    if not domain or is_school(address) or not school.active().LAW_FIRM_MAIL:
        return ""
    label = domain.rsplit(".", 2)[0] if domain.count(".") > 1 else domain.split(".")[0]
    return firm_domains().get(_flat(label), "")


def firm_recruiting(address: str) -> str:
    """Why this address counts as firm recruiting, or "" when it does not."""
    if is_school(address) or not school.active().LAW_FIRM_MAIL:
        return ""
    name = law_firm(address)
    if name:
        return name
    if RECRUITING_LOCAL.search(local_of(address)):
        return "a firm recruiting address"
    return ""


# Content that is nobody else's business, whatever the envelope says. Kept
# tight on purpose: every flyer footer in the broadcast carries the sentence
# "Accommodation requests related to a disability should be made by...", so
# disability wording alone must not veto an event or the whole feed empties.
PRIVATE_CONTENT = re.compile(
    r"\bbanner\s*id\b|\bgocard\s+number\b|"
    r"\b(?:student|loan|account)\s+number\b|"
    r"\bsocial\s+security\b|\bssn\b|"
    r"\b(?:stafford|grad\s*plus|federal)\s+loan\b|"
    r"\bloan\s+(?:decrease|disbursement|increase|amount|balance)\b|"
    r"\bdisbursement\s+notification\b|"
    r"\bfinancial\s+aid\s+(?:award|package|offer)\b|"
    r"\btuition\s+(?:bill|balance|payment|due)\b|\bbursar\b|"
    r"\byour\s+(?:grade|transcript|gpa|class\s+rank)\b|"
    r"\bdirect\s+deposit\b|\bpayroll\b|\bw-?2\b|"
    r"\bimmunization\s+record\b|\bmedical\s+record\b|"
    r"\btherapy\s+appointment\b|\bcounseling\s+appointment\b",
    re.I)

# "Your transcript" is somebody's record when the message is about it, and
# advice when it is one item in a list of what an employer reads: the Private
# Sector Pulse of 8 October 2026 was refused whole for "evaluating the results
# alongside your transcript, resume, and interviews". Only the record words
# are excused, only beside the other application materials, and never with a
# number in reach -- a GPA or a class rank is a number.
RECORD_WORDS = re.compile(r"your\s+(?:grade|transcript|gpa|class\s+rank)", re.I)
APPLICATION_MATERIALS = re.compile(
    r"\b(?:r[eé]sum[eé]s?|cover\s+letters?|writing\s+samples?|"
    r"application\s+materials?)\b", re.I)
RECORD_REACH = 40


def private_content(text: str):
    """The first PRIVATE_CONTENT match that is not advice, or None."""
    for hit in PRIVATE_CONTENT.finditer(text):
        if RECORD_WORDS.fullmatch(hit.group(0)):
            near = text[max(0, hit.start() - RECORD_REACH):hit.end() + RECORD_REACH]
            if APPLICATION_MATERIALS.search(near) and not re.search(r"\d", near):
                continue
        return hit
    return None

# Subjects that are a transaction with one person, not an announcement.
PRIVATE_SUBJECT = re.compile(
    r"^\s*(?:re|fwd?)\s*:|"                       # a reply is a conversation
    r"\binvitation:\s|\baccepted:\s|\bdeclined:\s|"   # calendar invites
    r"\bconfirmation\s+required\b|\byour\s+appointment\b|"
    r"\bcoffee\s+chat\b|\bbrief\s+chat\b|\binformational\s+interview\b",
    re.I)


def address_of(header: str) -> str:
    return email.utils.parseaddr(header or "")[1].strip().lower()


def domain_of(address: str) -> str:
    return address.rpartition("@")[2].lower()


def local_of(address: str) -> str:
    return address.rpartition("@")[0].lower()


def _within(domain: str, domains) -> bool:
    return any(domain == d or domain.endswith("." + d) for d in domains)


def is_broadcast_domain(address: str) -> bool:
    return _within(domain_of(address), school.active().BROADCAST_DOMAINS)


def is_school(address: str) -> bool:
    """Whether the address is at one of the school's own domains."""
    return _within(domain_of(address), school.active().DOMAINS)


def looks_personal(address: str) -> bool:
    """Whether the address belongs to a person rather than an office."""
    if is_broadcast_domain(address):
        return False
    return bool(school.active().person.search(local_of(address)))


def recipients(msg) -> list:
    out = []
    for field in ("To", "Cc", "Bcc", "Delivered-To", "X-Original-To"):
        for value in msg.get_all(field) or []:
            out.extend(a for _, a in email.utils.getaddresses([value]) if a)
    return [a.lower() for a in out]


def is_listserv(address: str) -> bool:
    """Whether the address is a list that reaches every student."""
    return bool(school.active().listservs.match(address or ""))


def to_a_listserv(msg) -> bool:
    """Addressed to a list: the school's (its profile's LISTSERVS) or one you
    added with `./fff sources add-list`."""
    return (any(is_listserv(a) for a in recipients(msg))
            or sources.list_for(msg) is not None)


# The one refusal that is about the address alone: nothing in the message
# kept it out. `./fff sources` offers senders refused for it as candidates.
NOT_RECOGNISED = "sender is not a recognised organisation"


def classify(msg, body: str = "", verdict: str = "") -> dict:
    """Whether this message may be read for public events, and why.

    Returns `{"allow", "reason", "sender", "org"}`. `reason` is written to be
    read in an audit: the point of the register is that a human can see why
    something was let in or kept out without re-deriving it. A reason for
    letting mail in always begins "from", and a reason for keeping it out
    never does; the register tells the two apart by it.

    `verdict` is a standing decision for this address: "allow" for a sender
    you added with `./fff sources add`, "deny" for one refused with
    `./fff senders deny` (see `sources.standing`).
    It is consulted **after** the private-content and personal-invite checks
    and can only widen the address rules, never the fail-safe: allowlisting a
    sender is a statement that they are an organisation, not permission to
    publish somebody's callback interview.
    """
    sender = address_of(msg.get("From", ""))
    subject = msg.get("Subject", "") or ""
    if not sender:
        return {"allow": False, "reason": "no sender", "sender": "", "org": ""}

    # Content veto runs first and beats everything. An org address that has
    # somehow ended up carrying an account number is still carrying one.
    haystack = f"{subject}\n{body or ''}"
    hit = private_content(haystack)
    if hit:
        return {"allow": False, "sender": sender, "org": "",
                "reason": f"personal information in the message ({hit.group(0)!r})"}

    # The fail-safe, before any question of who sent it. A trusted office
    # still sends "your callback interview", and a calendar invite is an
    # invitation to one person whoever it came from.
    private = personal.assess(msg, body, allowlisted=(verdict == "allow"))
    if private["personal"]:
        return {"allow": False, "sender": sender, "org": "",
                "reason": private["reason"]}

    # A list you added went to everyone on it, so who posted does not make
    # it private -- a classmate's post there is read, and credited to the list.
    listed = sources.list_for(msg)
    listserv = to_a_listserv(msg)
    broadcast = is_broadcast_domain(sender)

    if looks_personal(sender) and verdict != "allow" and not listed:
        return {"allow": False, "sender": sender, "org": "",
                "reason": "sent by a person, not an organisation"}

    if verdict == "deny":
        return {"allow": False, "sender": sender, "org": "",
                "reason": "refused by hand (./fff senders deny)"}

    # A firm mailing the class is an organisation, but only when it is
    # actually mailing the class: the same firm.com domain carries a partner
    # agreeing to coffee, and that is the reader's own business.
    firm = firm_recruiting(sender)
    if firm and not (broadcast or listserv or verdict == "allow"):
        if not personal.is_bulk(msg, body):
            return {"allow": False, "sender": sender, "org": "",
                    "reason": f"{firm} writing to one reader, not the class"}
        return {"allow": True, "sender": sender, "org": org_name(msg, sender),
                "reason": ("from %s" % firm if " " in firm
                           else "from a firm recruiting address")}

    if not (broadcast or listserv or is_school(sender) or verdict == "allow"):
        return {"allow": False, "sender": sender, "org": "",
                "reason": NOT_RECOGNISED}

    # A reply or a calendar invite is a conversation with this reader even
    # when the other end is an office.
    bad_subject = PRIVATE_SUBJECT.search(subject)
    if bad_subject and not listserv:
        return {"allow": False, "sender": sender, "org": "",
                "reason": f"addressed to one person ({bad_subject.group(0).strip()!r})"}

    why = (f"a list you added ({listed[0]})" if listed
           else "a student-body list" if listserv
           else "a broadcast system" if broadcast
           else "a sender you added" if verdict == "allow"
           else "an organisation address")
    return {"allow": True, "sender": sender, "org": org_name(msg, sender),
            "reason": f"from {why}"}


def org_name(msg, sender: str = "") -> str:
    """The organisation to credit, preferring the display name."""
    name, addr = email.utils.parseaddr(msg.get("From", ""))
    if name.strip():
        return name.strip()[:60]
    return local_of(addr or sender or "").replace("-", " ")[:60]


def allowed(msg, body: str = "") -> bool:
    return classify(msg, body)["allow"]
