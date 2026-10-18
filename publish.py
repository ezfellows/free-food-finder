"""Build the published page.

One self-contained HTML file plus a manifest of supporting files (flyer
images, the calendar feed). The Artifact platform serves supporting files from
the page's own origin, so flyers are referenced by relative path rather than
inlined as base64 -- no fourfold size inflation, and the image stays a real
image a QR code can be scanned from.

The page is wrapped in a doctype/head/body skeleton at publish time, so this
emits `<title>`, `<style>` and content only.

    python3 publish.py out.html      # writes the page, prints the manifest
"""

from __future__ import annotations

import base64
import html
import json
import os
import sys
from datetime import datetime, timedelta
from urllib.parse import quote, urlencode

import docket
import school
import store

ROOT = os.path.dirname(os.path.abspath(__file__))
PAGE_BUDGET = 12_000_000        # hard stop well under the platform's 16MB
FILE_CAP = 240                  # platform allows 255 entries per publish

TITLE = "Free Food Finder"

# The words at the top of the page belong to whoever runs it, not to the code.
# Set your own in data/config.json:
#
#     "page": {"title": "...", "standfirst": "<html>", "about": "data/about.html"}
#
# "about" names a file holding the introduction's HTML. Without them, these.
DEFAULT_STANDFIRST = ('Events with <strong class="k food">Free food</strong>, '
                      '<strong class="k net">Networking/Recruiting</strong> '
                      'value, or both.')
DEFAULT_ABOUT = """
      <p>Every event here was read out of the school&rsquo;s own announcements
        &mdash; its calendar, its student-life mailings, and the email of its
        student organisations and offices &mdash; and judged on two things,
        separately: whether there is free food, and whether there is someone
        worth meeting.</p>
      <p><strong class="k food">Food</strong> is judged on the wording
        (&ldquo;lunch will be provided&rdquo;). Expand &lsquo;Why it is
        listed&rsquo; on any event to see the exact phrase behind the call, and
        the original flyer where there is one.</p>
      <p><strong class="k net">Networking/recruiting</strong> is judged on who
        will be in the room: named firms, practising lawyers, recruiters,
        alumni. Check &lsquo;Why it is listed&rsquo; for whether an RSVP is
        needed.</p>

      <h4>Reading the tags</h4>
      <ul class="legend">
        <li><span class="tag food">Food confirmed</span> how sure the food is
          &mdash; confirmed, likely, possible &mdash; and what kind</li>
        <li><span class="tag net">Firm in the room</span> what kind of
          networking it is</li>
        <li><span class="tag firm">Law firm name</span> each firm named</li>
        <li><span class="tag org">Student org</span> the student org or office
          hosting it</li>
        <li><span class="tag need">Bring stuff</span> a resume, a dress code,
          an RSVP deadline</li>
        <li><span class="tag recep">Reception</span> a reception, or one to
          follow the talk</li>
        <li><span class="tag perk">Uber voucher</span> a ride or travel the
          host pays for, and whether you have to RSVP to get it</li>
        <li><span class="tag verified">Flyer supplied</span> there is an
          original poster to check, inside <em>Why it is listed</em></li>
        <li><span class="tag posted">Submitted</span> posted by a student
          rather than scraped, with their name on it</li>
      </ul>
      <p class="fine">Use <strong>Tags</strong> to filter on any of them at
        once &mdash; two firms shows events with either; a firm and a practice
        area shows only events with both.</p>
    """


def sources_html(profile) -> str:
    """Where the events come from, as the footer says it.

    Email first: it is where most events arrive. Student posts are credited
    in the footer's last line, beside the count.
    """
    parts = ["the emails student organisations, school offices and employers "
             "send to the school"]
    if profile.CALENDAR:
        parts.append(profile.CALENDAR_NAME or "the school&rsquo;s public events calendar")
    flyers = profile.FLYERS or {}
    if flyers:
        parts.append((flyers.get("description") or "the school&rsquo;s flyer newsletter")
                     + ", whose flyers are images and are read with OCR")
    listed = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return "Everything here is read out of " + listed + "."


def page_copy(cfg: dict = None) -> dict:
    """{placeholder: HTML} for the words that belong to a school or a person.

    The profile supplies the school's: its name and campus, the calendar and
    newsletter the footer credits, how far the other campus is, a room for
    the submit form's example. The config supplies the person's.
    """
    cfg = docket.load_config() if cfg is None else cfg
    mine = cfg.get("page") or {}
    profile = school.active()
    about = DEFAULT_ABOUT
    if mine.get("about"):
        where = mine["about"]
        where = where if os.path.isabs(where) else os.path.join(ROOT, where)
        with open(where, encoding="utf-8") as fh:
            about = fh.read()
    away = profile.AWAY_NAME or "Another campus"
    if profile.AWAY_TRAVEL:
        away += " \u00b7 " + profile.AWAY_TRAVEL
    travel = {"away": away, "offcampus": "Off campus", "virtual": "Online"}
    return {
        "__TITLE__": html.escape(mine.get("title") or TITLE),
        "__EYEBROW__": " &middot; ".join(
            html.escape(x) for x in (profile.NAME, profile.CAMPUS) if x),
        "__STANDFIRST__": mine.get("standfirst") or DEFAULT_STANDFIRST,
        "__ABOUT__": about,
        "__ROOM_EXAMPLE__": html.escape(profile.ROOM_EXAMPLE),
        "__SOURCES__": sources_html(profile),
        "__SCHOOL__": html.escape(profile.NAME) or "the school",
        # Inside a <script>: JSON, with "</" broken so it cannot close the tag.
        "__TRAVEL__": json.dumps(travel).replace("</", "<\\/"),
    }


def build(conn, days: int = 35, min_tier: str = "possible",
          submitted: dict = None) -> dict:
    """Returns {html, files, events, stamp}. Publishes nothing itself.

    `submitted` is carried through from the live page so a scheduled rebuild
    does not wipe what students have posted since the last one.
    """
    events = docket.build_events(conn, days=days, min_tier=min_tier)
    files = docket.flyer_manifest(events)

    if len(files) > FILE_CAP:                # oldest events lose their image
        for event in reversed(events):
            if len(files) <= FILE_CAP:
                break
            src = event.get("flyer_src")
            if src and src in files:
                del files[src]
                event["flyer_src"] = ""

    stamp = datetime.now().strftime("%-d %B %Y, %-I:%M %p")
    html = page(events, stamp, submitted)
    size = len(html.encode())
    if size > PAGE_BUDGET:
        raise RuntimeError(f"page is {size/1e6:.1f}MB, over the {PAGE_BUDGET/1e6:.0f}MB budget")
    return {"html": html, "files": files, "events": events, "stamp": stamp,
            "bytes": size}


GOOGLE_CALENDAR = "https://calendar.google.com/calendar/render"
OUTLOOK_CALENDAR = "https://outlook.office.com/calendar/0/deeplink/compose"


def calendar_links(event: dict, timezone: str = "") -> dict:
    """{"google", "outlook"}: links that open the event, filled in, in the
    reader's own calendar. {} when the event has no usable start.

    Links rather than a calendar file because a page in the artifact sandbox
    cannot hand a viewer an .ics: the downloads it may offer are images,
    documents, archives and text. A link is all either calendar needs.
    Times are the school's wall clock; Google is told which zone that is.
    """
    try:
        start = datetime.fromisoformat(event.get("starts_at") or "")
    except ValueError:
        return {}
    try:
        end = datetime.fromisoformat(event.get("ends_at") or "")
    except ValueError:
        end = None
    all_day = bool(event.get("all_day"))
    if all_day:
        # An all-day event ends the morning after its last day, in both.
        last = (end or start).date()
        span = (start.date(), last + timedelta(days=1))
    else:
        span = (start, end if end and end > start else start + timedelta(hours=1))
    title = event.get("title") or "Event"
    place = event.get("place") or event.get("location") or ""
    details = "\n".join(x for x in (
        "Hosted by " + event["org"] if event.get("org") else "",
        "RSVP: " + event["rsvp_url"] if event.get("rsvp_url") else "") if x)

    stamp = "%Y%m%d" if all_day else "%Y%m%dT%H%M%S"
    google = {"action": "TEMPLATE", "text": title,
              "dates": "/".join(t.strftime(stamp) for t in span)}
    if details:
        google["details"] = details
    if place:
        google["location"] = place
    if timezone and not all_day:
        google["ctz"] = timezone

    iso = "%Y-%m-%d" if all_day else "%Y-%m-%dT%H:%M:%S"
    outlook = {"path": "/calendar/action/compose", "rru": "addevent",
               "subject": title, "startdt": span[0].strftime(iso),
               "enddt": span[1].strftime(iso)}
    if all_day:
        outlook["allday"] = "true"
    if place:
        outlook["location"] = place
    if details:
        outlook["body"] = details
    encode = lambda params: urlencode(params, quote_via=quote)
    return {"google": f"{GOOGLE_CALENDAR}?{encode(google)}",
            "outlook": f"{OUTLOOK_CALENDAR}?{encode(outlook)}"}


def payload(events) -> str:
    """The event data the page renders from, safe to embed in a script tag."""
    timezone = school.active().TIMEZONE
    slim = []
    for e in events:
        slim.append({
            "id": e["id"], "title": e["title"], "start": e["starts_at"],
            "end": e["ends_at"], "allDay": bool(e["all_day"]),
            "place": e.get("place") or e.get("location") or "",
            "org": e.get("org") or "", "source": e.get("source_label") or "",
            "food": bool(e.get("matches_food")), "tier": e.get("tier") or "",
            "meals": e.get("meals") or [], "why": e.get("reason") or "",
            "evidence": e.get("signals") or [],
            "net": bool(e.get("matches_network")),
            "netKind": e.get("network_kind") or "",
            "netWhy": e.get("network_reason") or "",
            "areas": e.get("practice_areas") or [],
            "firms": [f["name"] for f in (e.get("firms") or [])],
            "notes": e.get("notes") or [], "rsvpBy": e.get("rsvp_by") or "",
            # The RSVP tracker: whether a sign-up is asked for, which tab it
            # sits under, and any ride or travel the host pays for.
            "rsvpNeeded": bool(e.get("rsvp_needed")),
            "rsvpClosed": bool(e.get("rsvp_closed")),
            "track": e.get("track") or "school",
            "perks": [{"label": p["label"], "withRsvp": bool(p.get("with_rsvp")),
                       "lead": p.get("lead_hours") or 0,
                       "phrase": p.get("phrase") or ""}
                      for p in (e.get("perks") or [])],
            "perkBy": e.get("perk_by") or "",
            # "reception", "after" (one follows a talk), or "".
            "reception": e.get("reception") or "",
            "virtual": bool(e.get("virtual")),
            "campus": e.get("campus") or "",
            "timeNote": e.get("time_note") or "",
            "foodNote": e.get("food_note") or "",
            # A promoted submission is an ordinary event that keeps its
            # author: scored, tagged and deduped like the rest, still saying
            # who posted it.
            "by": e.get("submitted_by") or "",
            "submitted": bool(e.get("submitted_by")),
            "verified": bool(e.get("submitted_by")),
            "rsvp": e.get("rsvp_url") or "", "flyer": e.get("flyer_src") or "",
            "cal": calendar_links(e, timezone),
            # No raw description. The page never showed it, and 400
            # characters of email carried the reader's address, netIDs and
            # per-recipient tracking links into the published source.
        })
    slim.sort(key=lambda x: x["start"] or "")
    return json.dumps(slim, separators=(",", ":")).replace("</", "<\\/")


# The page publishes itself when someone submits an event, and `publish()`
# takes a COMPLETE document. Serialising the live DOM is not allowed (and
# would bake in runtime state), so the page carries its own source: the full
# document, placeholders intact, base64-encoded to sidestep every escaping
# problem that nesting a document inside a <script> would otherwise cause.
DOC_HEAD = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "</head><body>")
DOC_TAIL = "</body></html>"


def page(events, stamp: str, submitted: dict = None, copy: dict = None) -> str:
    """The fragment the Artifact tool publishes, with its own source inside."""
    template = TEMPLATE
    # The words go in before the template is encoded, as the stamp does: the
    # copy the page republishes itself from must say the same things.
    for name, value in (copy if copy is not None else page_copy()).items():
        template = template.replace(name, value)
    template = template.replace("__STAMP__", stamp)
    full_document = DOC_HEAD + template + DOC_TAIL
    encoded = base64.b64encode(full_document.encode("utf-8")).decode("ascii")
    state = submitted if isinstance(submitted, dict) else {"posts": [], "blocked": []}
    # "</" must be escaped inside a <script> block or a submission titled
    # "</script>" closes the tag early, corrupting the page and losing every
    # post on the next rebuild. JSON treats \/ as /, so it reads back cleanly.
    submitted_json = json.dumps(state, separators=(",", ":")).replace("</", "<\\/")
    return (template
            .replace("__EVENTS__", payload(events))
            .replace("__SUBMITTED__", submitted_json)
            .replace("__TEMPLATE__", encoded))


TEMPLATE = r"""<title>__TITLE__</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Newsreader:ital,opsz,wght@0,6..72,400;0,6..72,500;0,6..72,600;1,6..72,400&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;450;500;600&display=swap">
<style>
:root{
  --paper:#f7f6f2; --card:#fffefb; --ink:#1b1a17; --ink-soft:#413e38;
  --muted:#6b675f; --rule:#ddd9d0; --rule-soft:#e9e6de;
  --food:#a85b12; --food-bg:#f6ebdd; --net:#3b3a8f; --net-bg:#e6e5f4;
  --flag:#8a2f2f; --flag-bg:#f7e7e7; --focus:#3b3a8f;
  --perk:#1d6a4c; --perk-bg:#e1efe7;
  --recep:#7d3266; --recep-bg:#f3e4ee;
  --serif:"Newsreader",Georgia,"Times New Roman",serif;
  --sans:"IBM Plex Sans",-apple-system,BlinkMacSystemFont,"Segoe UI",system-ui,sans-serif;
  --mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;
}
@media (prefers-color-scheme:dark){
  :root:not([data-theme="light"]){
    --paper:#141310; --card:#1c1b17; --ink:#eeeae0; --ink-soft:#cfc9bd;
    --muted:#9a948a; --rule:#302d26; --rule-soft:#262420;
    --food:#e29a4e; --food-bg:#2e2214; --net:#a3a0ef; --net-bg:#1e1d33;
    --flag:#e08585; --flag-bg:#33201f; --focus:#a3a0ef;
    --perk:#82cfa7; --perk-bg:#15291f;
    --recep:#e3a6d0; --recep-bg:#2e1b28;
  }
}
:root[data-theme="dark"]{
  --paper:#141310; --card:#1c1b17; --ink:#eeeae0; --ink-soft:#cfc9bd;
  --muted:#9a948a; --rule:#302d26; --rule-soft:#262420;
  --food:#e29a4e; --food-bg:#2e2214; --net:#a3a0ef; --net-bg:#1e1d33;
  --flag:#e08585; --flag-bg:#33201f; --focus:#a3a0ef;
  --perk:#82cfa7; --perk-bg:#15291f;
  --recep:#e3a6d0; --recep-bg:#2e1b28;
}
*{box-sizing:border-box}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);
  font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:760px;margin:0 auto;padding-block:30px 60px;
  padding-left:20px;padding-right:20px}
:focus-visible{outline:2px solid var(--focus);outline-offset:2px;border-radius:3px}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer}

.eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.14em;
  text-transform:uppercase;color:var(--muted)}
h1{font-family:var(--serif);font-weight:500;font-size:clamp(34px,7.5vw,46px);
  line-height:1.04;letter-spacing:-.018em;margin:10px 0 0;text-wrap:balance}
.standfirst{font-family:var(--serif);font-size:18px;line-height:1.5;
  color:var(--ink-soft);margin:12px 0 0;max-width:56ch}
.masthead{border-bottom:2px solid var(--ink);padding-bottom:22px;
  display:flex;flex-wrap:wrap;align-items:flex-start;gap:16px 24px}
.masthead .titles{flex:1 1 320px;min-width:0}
/* The submit control sits beside the title rather than above the lead card:
   "Next up" is what the page is for, and it stays where it was. */
.masthead .submitbar{flex:0 0 auto;margin:0;padding:0;border:0;
  display:flex;flex-direction:column;align-items:flex-end;gap:6px}
@media (max-width:560px){
  .masthead{flex-direction:column;gap:14px}
  /* flex-basis is the MAIN axis, which is height once this is a column --
     the 320px above became a 320px tall gap under the standfirst. */
  .masthead .titles{flex:0 0 auto}
  .masthead .submitbar{align-items:flex-start;width:100%}
}

.lead{margin-top:24px;padding:16px 18px;background:var(--card);
  border:1px solid var(--rule);border-left:3px solid var(--food)}
.lead .when{font-family:var(--mono);font-size:12px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--food)}
.lead h2{font-family:var(--serif);font-weight:500;font-size:21px;line-height:1.25;
  margin:5px 0 4px;text-wrap:balance}
.lead p{margin:0;color:var(--muted);font-size:13.5px}

.bar{display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;
  margin:28px 0 4px;padding-bottom:12px;border-bottom:1px solid var(--rule)}
.tabs{display:flex;gap:2px}
.tabs button{font-size:13.5px;font-weight:500;color:var(--muted);
  border-bottom:2px solid transparent;padding:4px 2px;margin-right:16px}
.tabs button[aria-pressed="true"]{color:var(--ink);border-bottom-color:var(--ink)}
/* Wraps, because the row grew past what a phone fits. Five controls just
   made it; the sixth ran off the right edge and was clipped rather than
   wrapped -- the parent .bar wraps its own children, which does nothing for
   these. */
.lenses{margin-left:auto;display:flex;flex-wrap:wrap;gap:6px}
.lenses button{font-family:var(--mono);font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;padding:4px 9px;border:1px solid var(--rule);
  color:var(--muted)}
.lenses button[aria-pressed="true"]{background:var(--ink);color:var(--paper);
  border-color:var(--ink)}

.about{margin-top:20px;border-left:3px solid var(--rule);padding-left:16px}
.about summary{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--muted);cursor:pointer;list-style:none}
.about summary::-webkit-details-marker{display:none}
.about summary::after{content:" \25be"}
.about[open] summary::after{content:" \25b4"}
.about summary:hover{color:var(--ink)}
.about summary:focus-visible{outline:2px solid var(--focus);outline-offset:3px}
.aboutbody{max-width:62ch}
.aboutbody p{font-size:13.5px;color:var(--ink-soft);margin:10px 0 0}
.aboutbody h4{font-family:var(--mono);font-size:10px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:500;margin:18px 0 0}
.k{font-style:normal}
.k.food{color:var(--food)}
.k.net{color:var(--net)}
.aboutbody .fine{font-size:12.5px;color:var(--muted)}
.aboutbody .sign{font-family:var(--serif);font-style:italic;
  color:var(--ink-soft);margin-top:4px}
.legend{list-style:none;margin:10px 0 0;padding:0;display:grid;gap:7px}
.legend li{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px;
  font-size:12.5px;color:var(--muted)}
.legend .tag{flex:0 0 auto}

.facets{margin-top:10px;border:1px solid var(--rule);background:var(--card)}
.facets.active{border-color:var(--net)}
.facets summary{font-family:var(--mono);font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--muted);padding:7px 10px;cursor:pointer;
  list-style:none}
.facets summary::-webkit-details-marker{display:none}
.facets summary::after{content:" \25be";color:var(--muted)}
.facets[open] summary::after{content:" \25b4"}
.facets.active summary{color:var(--net)}
.facets summary:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
#facetBody{padding:2px 10px 10px;border-top:1px solid var(--rule-soft)}
.facet{margin-top:10px}
.facet h4{font-family:var(--mono);font-size:9.5px;letter-spacing:.1em;
  text-transform:uppercase;color:var(--muted);font-weight:500;margin:0 0 5px}
.facet .opts{display:flex;flex-wrap:wrap;gap:5px}
.opt{display:inline-flex;align-items:center;gap:6px;cursor:pointer;
  font-size:12px;padding:3px 8px;border:1px solid var(--rule);border-radius:2px;
  color:var(--ink-soft);max-width:100%}
.opt:hover{border-color:var(--ink)}
.opt.on{background:var(--net-bg);border-color:transparent;color:var(--net)}
.opt input{margin:0;accent-color:var(--net);flex:0 0 auto}
.opt span:first-of-type{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.opt .n{font-family:var(--mono);font-size:10px;color:var(--muted)}
.opt.on .n{color:var(--net)}
#facetClear{margin-top:12px;font-size:12px}

.daybar{display:flex;align-items:baseline;gap:12px;margin:30px 0 0}
.daybar h3{font-family:var(--mono);font-size:11.5px;letter-spacing:.12em;
  text-transform:uppercase;color:var(--ink);font-weight:500;margin:0;
  white-space:nowrap}
.daybar .fill{flex:1;height:1px;background:var(--rule)}

.entry{display:grid;grid-template-columns:82px 1fr;gap:16px;padding:16px 0;
  border-bottom:1px solid var(--rule-soft)}
.entry .rail{font-family:var(--mono);font-size:12.5px;color:var(--muted);
  font-variant-numeric:tabular-nums;padding-top:2px}
.entry h4{font-family:var(--serif);font-weight:500;font-size:18px;line-height:1.3;
  margin:0;text-wrap:balance}
.entry .where{font-size:13px;color:var(--muted);margin-top:3px}
.entry .where .mono{font-family:var(--mono);font-size:12px}
.entry .where .travel{font-family:var(--mono);font-size:10.5px;
  letter-spacing:.06em;text-transform:uppercase;color:var(--flag);
  background:var(--flag-bg);padding:1px 5px;margin-left:5px;
  white-space:nowrap}

.tags{display:flex;flex-wrap:wrap;gap:6px;margin-top:9px}
.tag{font-family:var(--mono);font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;padding:3px 7px;border:1px solid var(--rule);
  color:var(--muted);white-space:nowrap;max-width:100%}
/* An org name can run to sixty characters off a flyer. Every other tag is a
   word or two and reads better unbroken, but a nowrap org tag drags the whole
   card past the screen -- one org's full name, off its own letterhead,
   pushed the page 118px wide at phone width. */
.tag.org{white-space:normal}
.tag.food{background:var(--food-bg);border-color:transparent;color:var(--food)}
.tag.net{background:var(--net-bg);border-color:transparent;color:var(--net)}
.tag.firm{background:var(--net);border-color:transparent;color:var(--paper);
  font-weight:500}
.tag.org{background:transparent;border-color:var(--rule);color:var(--ink-soft)}
.tag.need{background:var(--flag-bg);border-color:transparent;color:var(--flag)}
/* Something the host pays for -- a ride, a travel stipend. Green, because
   the red of a requirement is what stops you at the door and this is the
   opposite. */
.tag.perk{background:var(--perk-bg);border-color:transparent;color:var(--perk);
  font-weight:500}
/* A reception: the format a 1L is sent to, filled so it is the first thing
   read on the card. One that follows a talk is only tinted -- an hour of
   lecture comes first. */
.tag.recep{background:var(--recep);border-color:transparent;color:var(--paper);
  font-weight:500}
.tag.recep.after{background:var(--recep-bg);color:var(--recep)}

.acts{display:flex;flex-wrap:wrap;gap:14px;margin-top:11px;align-items:center}
.acts button,.acts a{font-size:12.5px;font-weight:500;color:var(--muted);
  text-decoration:underline;text-underline-offset:3px;
  text-decoration-color:var(--rule)}
.acts a.rsvp{color:var(--net);font-weight:600}
.acts .addcal{font-size:12.5px;color:var(--muted)}
.tag.need.urgent{font-weight:600}
.tag.closed{background:transparent;border-color:var(--rule);color:var(--muted)}
.acts button:hover,.acts a:hover{color:var(--ink)}

.why{margin-top:12px;padding:12px 14px;background:var(--card);
  border:1px solid var(--rule-soft);font-size:13px;color:var(--ink-soft)}
.why dl{margin:0;display:grid;grid-template-columns:auto 1fr;gap:4px 12px}
.why dt{font-family:var(--mono);font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;color:var(--muted);padding-top:3px}
.why dd{margin:0}
.why dd.dispute{color:var(--flag)}
.quote span{display:inline-block;font-family:var(--mono);font-size:11.5px;
  color:var(--muted);border-bottom:1px solid var(--rule);margin:0 9px 3px 0}
.flyer{width:100%;max-width:260px;margin-top:11px;border:1px solid var(--rule);
  border-radius:4px;display:block;cursor:zoom-in}
.flyer.zoom{position:fixed;inset:0;width:100vw;height:100vh;max-width:none;
  object-fit:contain;background:#000;z-index:60;margin:0;border:0;
  border-radius:0;cursor:zoom-out}

/* calendar */
.cal{margin-top:22px}
.cal h3{font-family:var(--serif);font-weight:500;font-size:20px;margin:0 0 10px}
.grid{display:grid;grid-template-columns:repeat(7,1fr);gap:1px;
  background:var(--rule);border:1px solid var(--rule)}
.dow{background:var(--paper);font-family:var(--mono);font-size:10px;
  letter-spacing:.08em;text-transform:uppercase;color:var(--muted);
  text-align:center;padding:6px 0}
.cell{background:var(--card);min-height:76px;padding:5px 5px 6px;
  display:flex;flex-direction:column;gap:3px}
.cell.blank{background:var(--paper)}
.cell .num{font-family:var(--mono);font-size:11px;color:var(--muted);
  font-variant-numeric:tabular-nums}
.cell.today .num{color:var(--food);font-weight:600}
.cell.has{cursor:pointer}
.cell.has:hover{background:color-mix(in srgb,var(--food) 7%,var(--card))}
.cell.sel{outline:2px solid var(--focus);outline-offset:-2px}
.pip{font-size:9.5px;line-height:1.3;padding:2px 3px;border-radius:2px;
  overflow:hidden;background:var(--food-bg);color:var(--food)}
.pip.net{background:var(--net-bg);color:var(--net)}
.pip .at{font-family:var(--mono);font-variant-numeric:tabular-nums;
  font-weight:500;white-space:nowrap}
.pip .at.short{display:none}
.pip .what{display:block;overflow:hidden;text-overflow:ellipsis;
  white-space:nowrap}
.more{font-family:var(--mono);font-size:9px;color:var(--muted)}
@media (max-width:520px){
  /* The title will not fit beside a time in a 38px cell, and the time is the
     half that decides whether you can go. */
  .cell{min-height:58px;padding:4px 3px 5px}
  .pip{font-size:9px;padding:1px 2px}
  .pip .at.long,.pip .what{display:none}
  .pip .at.short{display:block}
}
#flyerToggle{margin-left:8px;border-style:dashed}
#flyerToggle[aria-pressed="true"]{background:var(--food);color:var(--paper);
  border-color:var(--food);border-style:solid}
/* Pressed means showing, which is the default -- this one hides rather than
   reveals, so its unpressed state is the unusual one and gets the marking. */
#postToggle[aria-pressed="false"]{border-style:dashed;opacity:.65;
  text-decoration:line-through}
.picked{margin-top:18px;padding-top:14px;border-top:1px solid var(--rule)}
.picked .hint{color:var(--muted);font-size:13px}
.pickbar{display:flex;align-items:center;gap:10px;flex-wrap:wrap;
  margin-bottom:6px}
.pickbar button{font-family:var(--mono);font-size:10.5px;letter-spacing:.07em;
  text-transform:uppercase;padding:4px 9px;border:1px solid var(--rule);
  color:var(--muted);white-space:nowrap}
.pickbar button:hover:not(:disabled){color:var(--ink);border-color:var(--ink)}
.pickbar button:disabled{opacity:.4;cursor:default}
.pickbar button.clear{margin-left:auto;border-style:dashed}
.pickday{display:flex;flex-direction:column;line-height:1.25}
.pickday strong{font-family:var(--serif);font-weight:500;font-size:17px}
.pickday .count{font-family:var(--mono);font-size:10.5px;letter-spacing:.06em;
  text-transform:uppercase;color:var(--muted)}
@media (max-width:460px){
  .pickbar .pickday{order:-1;flex-basis:100%}
  .pickbar button.clear{margin-left:0}
}

.submitbar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;
  margin-top:26px;padding-top:18px;border-top:1px solid var(--rule)}
button.primary{background:var(--ink);color:var(--paper);font-size:13.5px;
  font-weight:600;padding:9px 16px;border-radius:3px}
button.quiet{font-size:13px;color:var(--muted);text-decoration:underline;
  text-underline-offset:3px;text-decoration-color:var(--rule)}
button.wide{width:100%;margin-top:18px;padding:12px}
.sheet{border:1px solid var(--rule);background:var(--paper);color:var(--ink);
  max-width:440px;width:calc(100% - 32px);padding:22px;border-radius:4px}
.sheet::backdrop{background:rgba(0,0,0,.5)}
.sheet h3{font-family:var(--serif);font-weight:500;font-size:21px;margin:0 0 6px}
.sheet .x{position:absolute;top:10px;right:12px;font-size:22px;color:var(--muted);
  line-height:1}
.sheethint{font-size:12.5px;color:var(--muted);margin:0 0 14px;line-height:1.5}
.sheet label{display:block;font-family:var(--mono);font-size:10px;
  letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
  margin:13px 0 5px}
.sheet label .opt{text-transform:none;letter-spacing:0}
.sheet input,.sheet textarea{width:100%;font:inherit;font-size:14px;
  color:var(--ink);background:var(--card);border:1px solid var(--rule);
  padding:9px 10px;border-radius:3px}
.sheet textarea{resize:vertical}
.modrow{display:flex;justify-content:space-between;align-items:center;gap:12px;
  padding:9px 0;border-bottom:1px solid var(--rule-soft);font-size:13.5px}
.modrow button{font-size:12px;color:var(--flag);text-decoration:underline;
  text-underline-offset:3px}
.tag.unverified{background:transparent;border-style:dashed;
  border-color:color-mix(in srgb,var(--flag) 45%,var(--rule));color:var(--flag)}
.tag.verified{background:transparent;
  border-color:color-mix(in srgb,var(--net) 45%,var(--rule));color:var(--net)}
.tag.posted{background:transparent;border-style:dashed;color:var(--muted)}
.entry .where .by{color:var(--ink-soft)}
.fieldnote{font-size:11.5px;color:var(--muted);margin:5px 0 0;line-height:1.4}
.empty{padding:44px 0;text-align:center;color:var(--muted)}
.empty strong{display:block;font-family:var(--serif);font-size:20px;
  color:var(--ink);font-weight:500;margin-bottom:6px}
.notes{margin-top:44px;padding-top:22px;border-top:2px solid var(--ink)}
.notes h3{font-family:var(--serif);font-weight:500;font-size:20px;margin:0 0 10px}
.notes p{color:var(--ink-soft);font-size:13.5px;max-width:62ch;margin:0 0 11px}
.notes .fine{color:var(--muted);font-size:12.5px}
@media (max-width:460px){
  .entry{grid-template-columns:1fr;gap:6px}
  .entry .rail{padding-top:0}
  .lenses{margin-left:0}
}

/* The RSVP tracker. One element: under the "Next up" card it is a folding
   panel, and on a screen wide enough for two columns it moves beside the
   docket and stays in view. The rows are worked out as the page is read, so
   a page built a week ago still counts down to the right day. */
.rsvps{margin-top:18px;border:1px solid var(--rule);background:var(--card)}
/* The arrow keeps to the title's row: wrapped onto a line of its own, it
   left a phone an empty-looking third line. */
.rsvphead{display:grid;grid-template-columns:minmax(0,1fr) auto;
  align-items:baseline;gap:3px 10px;width:100%;padding:9px 12px;
  text-align:left}
.rsvphead > *{grid-column:1}
.rsvphead::after{content:"\25be";grid-column:2;grid-row:1;color:var(--muted)}
.rsvps.open .rsvphead::after{content:"\25b4"}
.rsvphead:focus-visible{outline:2px solid var(--focus);outline-offset:-2px}
.rsvps:not(.open) .rsvpbody{display:none}
.rsvptitle{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;
  text-transform:uppercase;color:var(--ink);font-weight:500}
.rsvpsum{font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
.rsvpbody{padding:2px 12px 12px;border-top:1px solid var(--rule-soft)}
.sidehead{display:none}
.rsvptabs{display:flex;gap:2px;margin:10px 0 2px;border-bottom:1px solid var(--rule)}
.rsvptabs button{font-size:13px;font-weight:500;color:var(--muted);
  border-bottom:2px solid transparent;padding:4px 2px;margin:0 14px -1px 0}
.rsvptabs button[aria-pressed="true"]{color:var(--ink);border-bottom-color:var(--ink)}
.rsvptabs .n{font-family:var(--mono);font-size:10.5px;color:var(--muted);
  margin-left:4px;font-variant-numeric:tabular-nums}
/* The docket's lens bar in small: every event, or receptions only. */
.rsvpshow{display:flex;gap:6px;margin-top:10px}
.rsvpshow button{font-family:var(--mono);font-size:10px;letter-spacing:.07em;
  text-transform:uppercase;padding:3px 8px;border:1px solid var(--rule);
  color:var(--muted)}
.rsvpshow button[aria-pressed="true"]{background:var(--ink);color:var(--paper);
  border-color:var(--ink)}
.rsvpshow button[data-rshow="reception"][aria-pressed="true"]{
  background:var(--recep);border-color:var(--recep)}
.rgroup{margin-top:12px}
.rgroup h3,.rfold > summary{font-family:var(--mono);font-size:9.5px;
  letter-spacing:.1em;text-transform:uppercase;color:var(--muted);
  font-weight:500;margin:0}
.rgroup.soon h3{color:var(--flag)}
.rfold{margin-top:14px}
.rfold > summary{cursor:pointer;list-style:none;padding:2px 0}
.rfold > summary::-webkit-details-marker{display:none}
.rfold > summary::after{content:" \25be"}
.rfold[open] > summary::after{content:" \25b4"}
.rfold > summary:hover{color:var(--ink)}
.rrow{display:grid;grid-template-columns:auto minmax(0,1fr);gap:0 10px;
  padding:9px 0;border-bottom:1px solid var(--rule-soft)}
.rrow input{margin:3px 0 0;width:16px;height:16px;accent-color:var(--net);
  cursor:pointer}
.rtitle{font-family:var(--serif);font-weight:500;font-size:15.5px;line-height:1.3;
  text-wrap:balance;overflow-wrap:anywhere}
.rmeta{font-size:12px;color:var(--muted);margin-top:2px;overflow-wrap:anywhere}
.rdue{font-family:var(--mono);font-size:10.5px;letter-spacing:.02em;
  color:var(--ink-soft);margin-top:4px;font-variant-numeric:tabular-nums}
.rdue.urgent{color:var(--flag);font-weight:600}
.rdue.none{color:var(--muted)}
.rdue.perkdue{color:var(--perk)}
.rdue.perkdue.urgent{color:var(--flag)}
.rrow .tags{margin-top:6px}
.racts{margin-top:5px}
.racts a{font-size:12.5px;font-weight:600;color:var(--net);
  text-decoration:underline;text-underline-offset:3px;
  text-decoration-color:var(--rule)}
.racts a:hover{color:var(--ink)}
.rrow.done .rtitle{color:var(--muted);text-decoration:line-through;
  text-decoration-color:var(--rule)}
.rempty{font-size:13px;color:var(--muted);margin:12px 0 0}
.rsvpfine{font-size:11.5px;color:var(--muted);margin:14px 0 0}

/* Two columns once there is room for both: the tracker beside the docket,
   which keeps the width it always had where there is room and narrows where
   there is not. From 960px: at 1180 the sidebar sat under the docket as a
   folded bar in a 1024px window -- a laptop, or the Claude app's preview pane
   -- below the fold, and a reader looking for it saw nothing. Every row of the
   grid is its own height except the last, which takes whatever the tracker
   needs beyond the docket -- so a short docket never opens gaps between its
   own sections. */
@media (min-width:960px){
  .wrap{max-width:1140px;display:grid;
    grid-template-columns:minmax(0,760px) 300px;
    column-gap:clamp(24px,3vw,40px);
    grid-template-rows:repeat(9,auto) 1fr;justify-content:center;
    align-items:start}
  .wrap > :not(dialog){grid-column:1}
  .wrap > .masthead{grid-column:1 / -1}
  .wrap > .rsvps{grid-column:2;grid-row:2 / -1;position:sticky;
    top:calc(env(safe-area-inset-top,0px) + 16px);
    max-height:calc(100vh - 32px);overflow:auto;overscroll-behavior:contain;
    margin-top:20px;border:0;border-left:1px solid var(--rule);
    background:transparent}
  /* Shown by the stylesheet alone, whatever the panel was left as on a
     phone: a script told of the widening could miss it -- a hidden or
     throttled tab is never told -- and the sidebar would collapse to
     nothing with no way to open it. */
  .rsvphead{display:none}
  #rsvps .rsvpbody{display:block;border-top:0;padding:0 0 4px 20px}
  .sidehead{display:block;font-family:var(--serif);font-weight:500;
    font-size:20px;line-height:1.2;margin:0}
  .sidehead .rsvpsum{display:block;font-family:var(--sans);font-size:12.5px;
    margin-top:3px}
}
</style>

<div class="wrap">
  <header class="masthead">
    <div class="titles">
      <div class="eyebrow">__EYEBROW__</div>
      <h1>__TITLE__</h1>
      <p class="standfirst">__STANDFIRST__</p>
    </div>
    <div id="submitBar" class="submitbar" hidden>
      <button type="button" id="openSubmit" class="primary">+ Submit an event</button>
      <button type="button" id="openModerate" class="quiet">Manage submissions</button>
    </div>
  </header>

  <details class="about" id="about" open>
    <summary>What this is, and how to read it</summary>
    <div class="aboutbody">__ABOUT__</div>
  </details>

  <div id="lead"></div>

  <section id="rsvps" class="rsvps" aria-labelledby="rsvpTitle">
    <button type="button" class="rsvphead" id="rsvpToggle"
            aria-expanded="false" aria-controls="rsvpBody">
      <span class="rsvptitle" id="rsvpTitle">RSVPs</span>
      <span class="rsvpsum" id="rsvpSum"></span></button>
    <div class="rsvpbody" id="rsvpBody">
      <h2 class="sidehead">RSVPs <span class="rsvpsum" id="rsvpSide"></span></h2>
      <div class="rsvptabs" id="rsvpTabs" role="group" aria-label="Which RSVPs">
        <button type="button" data-track="firm" aria-pressed="true">Firms<span class="n"></span></button>
        <button type="button" data-track="school" aria-pressed="false">Law school<span class="n"></span></button>
      </div>
      <div class="rsvpshow" id="rsvpShow" role="group" aria-label="Show">
        <button type="button" data-rshow="all" aria-pressed="true">All</button>
        <button type="button" data-rshow="reception" aria-pressed="false">Receptions</button>
      </div>
      <div id="rsvpList"></div>
      <p class="rsvpfine">Tick an event once you have RSVP&rsquo;d. Ticks are
        saved in this browser only.</p>
    </div>
  </section>

  <div class="bar">
    <div class="tabs" id="views" role="group" aria-label="View">
      <button type="button" data-view="list" aria-pressed="true">Docket</button>
      <button type="button" data-view="cal" aria-pressed="false">Calendar</button>
    </div>
    <div class="lenses" id="lenses" role="group" aria-label="Filter">
      <button type="button" data-lens="all" aria-pressed="true">All</button>
      <button type="button" data-lens="food" aria-pressed="false">Food</button>
      <button type="button" data-lens="net" aria-pressed="false">Networking</button>
      <button type="button" data-lens="reception" aria-pressed="false">Receptions</button>
      <button type="button" data-lens="virtual" aria-pressed="false">Virtual</button>
      <button type="button" id="flyerToggle" aria-pressed="false">Flyers</button>
      <button type="button" id="postToggle" aria-pressed="true">Student posts</button>
    </div>
  </div>
  <details id="facets" class="facets">
    <summary><span id="facetLabel">Tags</span></summary>
    <div id="facetBody"></div>
  </details>

  <main id="list"></main>
  <section id="cal" class="cal" hidden></section>

  <dialog id="submitSheet" class="sheet">
    <form method="dialog"><button class="x" value="cancel" aria-label="Close">&times;</button></form>
    <h3>Submit an event</h3>
    <p class="sheethint">Everyone reading this page sees what you post. A flyer
      is what makes it checkable &mdash; without one it is marked unverified.</p>
    <label for="sName">Your name</label>
    <input id="sName" type="text" maxlength="40" autocomplete="name"
           placeholder="So posts can be traced back">
    <p class="fieldnote">Shown publicly on every post you make.</p>
    <label for="sTitle">Event</label>
    <input id="sTitle" type="text" maxlength="90" placeholder="Pizza and Policy">
    <label for="sWhen">When</label>
    <input id="sWhen" type="datetime-local">
    <label for="sPlace">Where</label>
    <input id="sPlace" type="text" maxlength="60" placeholder="__ROOM_EXAMPLE__">
    <label for="sNote">What is on offer <span class="opt">(optional)</span></label>
    <textarea id="sNote" maxlength="240" rows="2"
              placeholder="Free pizza, business casual, RSVP by Friday"></textarea>
    <label for="sLink">Flyer or RSVP link <span class="opt">(optional)</span></label>
    <input id="sLink" type="url" maxlength="300" placeholder="https://">
    <button type="button" id="doSubmit" class="primary wide">Post it</button>
    <p class="sheethint" id="submitMsg"></p>
  </dialog>

  <dialog id="moderateSheet" class="sheet">
    <form method="dialog"><button class="x" value="cancel" aria-label="Close">&times;</button></form>
    <h3>Manage submissions</h3>
    <p class="sheethint">Removing a person takes down every post of theirs and
      blocks further ones from that name.</p>
    <div id="modList"></div>
  </dialog>

  <section class="notes">
    <h3>How an event gets on the docket</h3>
    <p>__SOURCES__ A scorer then looks for two
      things independently. <strong>Food</strong> comes from phrases that promise it,
      minus the ones that take it away, like &ldquo;brown bag&rdquo;.
      <strong>Networking</strong> comes from who will be in the room: named firms,
      practising lawyers, recruiters, alumni.</p>
    <p>The two never gate each other. A clerkship panel that says <em>no food will be
      provided</em> still belongs here. Expand any entry to see the exact phrases
      behind the call, and the original flyer where there is one.</p>
    <p class="fine">Requirements &mdash; dress code, resume collection, RSVP deadlines
      &mdash; are shown when the event states them. Nothing is inferred: if it is not
      written down, it is not shown.</p>
    <p class="fine">The RSVP list holds every event whose announcement asks you to
      sign up, soonest first: by its deadline where one is stated, otherwise by
      the event itself. A reception, a ride voucher or a travel stipend is
      tagged only when the announcement names one.</p>
    <p class="fine">Unofficial and student-built. Not affiliated with, endorsed by,
      or speaking for __SCHOOL__. Check the flyer before you walk over.
      <span id="count"></span> scraped events, taken <strong>__STAMP__</strong>,
      plus whatever students have submitted since.</p>
  </section>
</div>

<script type="application/json" id="event-data">__EVENTS__</script>
<script type="application/json" id="submitted-data">__SUBMITTED__</script>
<script type="text/plain" id="self-template">__TEMPLATE__</script>
<script>
(function () {
  "use strict";
  var EVENTS = [];
  try { EVENTS = JSON.parse(document.getElementById("event-data").textContent); }
  catch (e) { EVENTS = []; }

  // Submissions live in the page itself: the page IS the record. `posts` is
  // what people added, `blocked` is names whose posts were taken down.
  var SUB = {posts: [], blocked: []};
  try {
    var raw = JSON.parse(document.getElementById("submitted-data").textContent);
    if (raw && Array.isArray(raw.posts)) SUB = raw;
  } catch (e) {}
  var writer = null;          // the artifact capability, once it resolves

  var KEY_VIEW = "docket-view", KEY_LENS = "docket-lens",
      KEY_FLYERS = "docket-flyers", KEY_POSTS = "docket-posts",
      KEY_FACETS = "docket-facets", KEY_ABOUT = "docket-about",
      KEY_RSVP_TAB = "docket-rsvp-tab", KEY_RSVP_OPEN = "docket-rsvp-open",
      KEY_RSVPED = "docket-rsvped", KEY_RSVP_SHOW = "docket-rsvp-show";
  function read(k, d) { try { var v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function write(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }

  // What a tag can be filtered on, and where to find it on an event. Order
  // is the order the menu lists them in.
  var FACETS = [
    ["org",  "Organisation",  function (e) { return e.org ? [e.org] : []; }],
    ["firm", "Firm",          function (e) { return e.firms || []; }],
    ["area", "Practice area", function (e) { return e.areas || []; }],
    ["meal", "Food",          function (e) { return e.meals || []; }],
    ["note", "Requirements",  function (e) { return e.notes || []; }],
    // Filtered on the kind of offer, not the amount: "$20 Uber voucher" and
    // "Uber voucher" are the same thing to someone looking for a ride home.
    ["perk", "Perks",         function (e) {
      return (e.perks || []).map(function (p) { return perkKind(p.label); });
    }]
  ];

  function perkKind(label) {
    var bare = String(label || "").replace(/^\$\S+\s+/, "");
    return bare.charAt(0).toUpperCase() + bare.slice(1);
  }

  function readFacets() {
    var out = {};
    FACETS.forEach(function (f) { out[f[0]] = new Set(); });
    try {
      var saved = JSON.parse(read(KEY_FACETS, "{}"));
      FACETS.forEach(function (f) {
        if (Array.isArray(saved[f[0]])) out[f[0]] = new Set(saved[f[0]]);
      });
    } catch (e) {}
    return out;
  }

  function writeFacets() {
    var plain = {};
    // Array.from, not slice: a Set has no `length`, so the array-like path
    // returns [] and every selection is silently lost on reload.
    FACETS.forEach(function (f) { plain[f[0]] = Array.from(state.facets[f[0]]); });
    write(KEY_FACETS, JSON.stringify(plain));
  }

  function facetCount() {
    return FACETS.reduce(function (n, f) { return n + state.facets[f[0]].size; }, 0);
  }

  function matchesFacets(e) {
    // Any within a group, all across them. Ticking a second firm widens the
    // list; ticking a practice area as well narrows it. A group with nothing
    // ticked asks nothing of the event.
    return FACETS.every(function (f) {
      var picked = state.facets[f[0]];
      if (!picked.size) return true;
      return f[2](e).some(function (v) { return picked.has(v); });
    });
  }

  var state = {
    view: read(KEY_VIEW, "list"),
    lens: read(KEY_LENS, "all"),
    // Flyers are posters: informative, but 22 of them turn the docket into a
    // scroll. Off by default, one tap to bring them all back -- and with it
    // off a single flyer is still one tap away, inside "Why it is listed".
    flyers: read(KEY_FLYERS, "0") === "1",
    // Student posts are shown by default -- they are the point of letting
    // people post. The toggle takes them out of view for a reader who only
    // trusts the scraped feed.
    posts: read(KEY_POSTS, "1") === "1",
    facets: readFacets(),
    open: new Set(),
    day: null,
    // The RSVP tracker's tab, and whether its folded groups are open --
    // kept here because the list is redrawn every minute.
    track: read(KEY_RSVP_TAB, "firm"),
    rshow: read(KEY_RSVP_SHOW, "all"),
    folds: {closed: false, done: false}
  };
  if (["list", "cal"].indexOf(state.view) < 0) state.view = "list";
  if (["all", "food", "net", "reception", "virtual"].indexOf(state.lens) < 0) state.lens = "all";
  if (["firm", "school"].indexOf(state.track) < 0) state.track = "firm";
  if (["all", "reception"].indexOf(state.rshow) < 0) state.rshow = "all";

  // Events this reader has ticked as RSVP'd. This browser only: nobody else
  // sees them, and they do not follow the reader to another device. Ticks
  // for events no longer on the page are dropped, so the list cannot grow
  // without end.
  var rsvped = (function () {
    var saved = [];
    try { saved = JSON.parse(read(KEY_RSVPED, "[]")); } catch (e) {}
    var listed = new Set(EVENTS.map(function (e) { return String(e.id); }));
    return new Set((Array.isArray(saved) ? saved : []).map(String)
      .filter(function (id) { return listed.has(id); }));
  })();
  function writeTicks() { write(KEY_RSVPED, JSON.stringify(Array.from(rsvped))); }

  var NET = {recruiting:"Recruiting", firm:"Firm in the room",
             practitioner:"Lawyers present", alumni:"Alumni"};
  var TIER = {confirmed:"Food confirmed", likely:"Food likely",
              possible:"Food possible"};
  var DOW = ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"];

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, function (c) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];
    });
  }
  function when(e) { return new Date(e.start); }
  function fmtTime(d) {
    return d.toLocaleTimeString([], {hour:"numeric", minute:"2-digit"})
            .replace(/\s?([AP])M/i, function (m, p) { return p.toLowerCase() + "m"; });
  }
  // "12:00pm" does not fit a calendar cell on a phone -- at 375px a cell is
  // about 38px wide. Dropping the :00 and the m gets "12p" and "3:30p" into
  // roughly 33px, which is the difference between a readable month and a row
  // of coloured bars that only say "something is on".
  function fmtTimeShort(d) {
    var h = d.getHours(), m = d.getMinutes();
    var suffix = h < 12 ? "a" : "p";
    var hour = h % 12 || 12;
    return hour + (m ? ":" + (m < 10 ? "0" : "") + m : "") + suffix;
  }
  // "5:30-7:00pm". The end time answers the question the start cannot: not
  // "can I get there" but "is it worth going at all, with a 4:45 class".
  // The meridiem is printed once when both ends share it, because
  // "5:30pm-7:00pm" spends eight characters saying pm twice.
  function fmtSpan(e) {
    var start = when(e);
    if (e.allDay) return "all day";
    var text = fmtTime(start);
    if (!e.end) return text;
    var end = new Date(e.end);
    if (isNaN(end) || end <= start) return text;
    var sameHalf = (start.getHours() < 12) === (end.getHours() < 12);
    var first = sameHalf ? text.replace(/[ap]m$/, "") : text;
    return first + "\u2013" + fmtTime(end);
  }
  function dayKey(d) { return d.getFullYear() + "-" + (d.getMonth()+1) + "-" + d.getDate(); }
  function dayLabel(d) {
    var now = new Date();
    var diff = Math.round((new Date(d.toDateString()) - new Date(now.toDateString())) / 864e5);
    if (diff === 0) return "Today · " + d.toLocaleDateString([], {month:"short", day:"numeric"});
    if (diff === 1) return "Tomorrow · " + d.toLocaleDateString([], {month:"short", day:"numeric"});
    return d.toLocaleDateString([], {weekday:"long", month:"short", day:"numeric"});
  }
  function countdown(d, allDay) {
    var mins = Math.round((d - new Date()) / 60000);
    if (mins < 0) return "happening now";
    if (allDay && mins > 0) { var n = Math.ceil(mins/1440); return n <= 1 ? "tomorrow" : "in " + n + " days"; }
    if (mins < 60) return "in " + mins + " min";
    if (mins < 1440) { var h = Math.floor(mins/60); return "in " + h + (mins%60 ? "h " + (mins%60) + "m" : " hour" + (h>1?"s":"")); }
    var dd = Math.round(mins/1440);
    return dd <= 1 ? "tomorrow" : "in " + dd + " days";
  }

  function blocked(name) {
    return SUB.blocked.indexOf(String(name || "").trim().toLowerCase()) >= 0;
  }

  function asEvent(post) {
    return {
      id: "sub-" + post.id, title: post.title, start: post.start, end: null,
      allDay: false, place: post.place || "",
      // `org` is where a HOST goes. Putting the submitter there rendered
      // the room and then the poster's name, which reads as though they ran it.
      org: "",
      source: "Submitted by " + (post.by || "someone"),
      food: true, net: true,          // unscored, so it shows under every lens
      tier: "", meals: [], why: "", evidence: [], netKind: "", netWhy: "",
      areas: [], firms: [], notes: [], rsvp: post.link || "", flyer: "",
      desc: post.note || "", submitted: true, by: post.by || "",
      // Something a reader can open and check for themselves. Not an upload:
      // the artifact `assets` capability would take files, but declaring it
      // makes the page organization-internal and the public link stops
      // working. A flyer already lives somewhere with a URL.
      verified: !!post.link
    };
  }

  function everything() {
    return EVENTS.concat(SUB.posts.filter(function (p) {
      return !blocked(p.by);
    }).map(asEvent));
  }

  function beforeFacets() {
    var floor = Date.now() - 2*3600*1000;
    return everything().filter(function (e) {
      if (when(e).getTime() < floor) return false;
      if (e.submitted && !state.posts) return false;
      if (state.lens === "food") return e.food;
      if (state.lens === "net") return e.net;
      if (state.lens === "reception") return !!e.reception;
      if (state.lens === "virtual") return e.virtual;
      return true;
    });
  }

  function visible() {
    var floor = Date.now() - 2*3600*1000;
    // Sorted here, not upstream: the feed arrives in order but submissions are
    // concatenated onto the end of it, so a student posting an event for next
    // Tuesday lands after October. Unsorted, the day headings repeat, the
    // "next up" card looks at the wrong event, and stepping between days in
    // the calendar walks them out of order.
    return everything().filter(function (e) {
      if (when(e).getTime() < floor) return false;
      // Here rather than in the list renderer, so hiding posts takes them out
      // of the calendar and the "next up" card at the same time.
      if (e.submitted && !state.posts) return false;
      if (!matchesFacets(e)) return false;
      if (state.lens === "food") return e.food;
      if (state.lens === "net") return e.net;
      if (state.lens === "reception") return !!e.reception;
      if (state.lens === "virtual") return e.virtual;
      return true;
    }).sort(function (a, b) { return when(a) - when(b); });
  }

  // How far it is. The home campus is the default and says nothing; anything
  // else is a journey the reader should know about before setting off. The
  // page used to show the school's own name for a reception across town. The
  // other campus's name and distance come from the school's profile.
  var TRAVEL = __TRAVEL__;
  function travel(e) {
    var note = TRAVEL[e.campus];
    if (!note) return "";
    return ' <span class="travel">' + esc(note) + "</span>";
  }

  // A reception, or one after a talk. First on the card and filled: it is
  // the format a 1L is sent to.
  function recepTag(e) {
    if (e.reception === "after") return ["recep after", "Reception to follow"];
    return e.reception ? ["recep", "Reception"] : null;
  }

  function tags(e) {
    var out = [];
    var recep = recepTag(e);
    if (recep) out.push(recep);
    if (e.food && TIER[e.tier]) out.push(["food", TIER[e.tier]]);
    (e.meals || []).forEach(function (m) { out.push(["food", m]); });
    if (e.net && NET[e.netKind]) out.push(["net", NET[e.netKind]]);
    // No longer capped at two: the filter can select any of them, and a card
    // showing the first two of three made the third look like it was missing.
    (e.areas || []).forEach(function (a) { out.push(["net", a]); });
    // Filled rather than outlined. A firm name is the thing a reader is
    // hunting for; "Lawyers present" is a description of the room.
    (e.firms || []).forEach(function (f) { out.push(["firm", f]); });
    if (e.org && !e.submitted) out.push(["org", e.org]);
    // The poster this was read off. Worth saying on the card: with the
    // Flyers toggle off the image lives inside "Why it is listed", so
    // nothing otherwise tells a reader there is one to check.
    if (e.flyer) out.push(["verified", "Flyer supplied"]);
    // A ride or travel the host pays for, and what it takes to get it.
    (e.perks || []).forEach(function (p) { out.push(["perk", perkText(p)]); });
    // Requirements read as warnings: they are what stops you at the door.
    // The RSVP deadline among them is worked out as the page is read, not
    // when it was built: a page a week old must not still invite an RSVP
    // that closed on Tuesday.
    var deadline = rsvpTag(e);
    (e.notes || []).forEach(function (n) {
      out.push(deadline && /^RSVP by /.test(n) ? deadline : ["need", n]);
    });
    // "RSVPs have closed", said in so many words. Once is enough when a
    // passed deadline has already said it.
    if (e.rsvpClosed && !(deadline && deadline[1] === "RSVP closed"))
      out.push(["closed", "RSVP closed"]);
    if (e.submitted) {
      out.push(["posted", "Submitted"]);
      out.push(e.verified ? ["verified", "Verified \u2014 link"]
                          : ["unverified", "Unverified \u2014 no proof"]);
    }
    return out.map(function (t) {
      return '<span class="tag ' + t[0] + '">' + esc(t[1]) + "</span>";
    }).join("");
  }

  // `rsvpBy` is a date ("2026-10-16"); the deadline is the end of that day.
  // Null leaves the note as built ("RSVP by 16 Oct").
  function rsvpTag(e) {
    var p = (e.rsvpBy || "").split("-");
    if (p.length !== 3) return null;
    var day = new Date(+p[0], +p[1] - 1, +p[2]);
    if (isNaN(day.getTime())) return null;
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var days = Math.round((day - today) / 86400000);
    if (days < 0) return ["closed", "RSVP closed"];
    if (days === 0) return ["need urgent", "RSVP by today"];
    if (days === 1) return ["need urgent", "RSVP by tomorrow"];
    return null;
  }

  // --- the RSVP tracker --------------------------------------------------

  // "Lyft voucher · RSVP 48h ahead": the offer, then what it takes.
  function perkText(p) {
    return p.label + (p.lead ? " · RSVP " + p.lead + "h ahead"
                             : p.withRsvp ? " · with RSVP" : "");
  }

  // The end of the stated deadline day, as a local time; null without one.
  function deadlineEnd(e) {
    var p = (e.rsvpBy || "").split("-");
    if (p.length !== 3) return null;
    var day = new Date(+p[0], +p[1] - 1, +p[2], 23, 59, 59);
    return isNaN(day.getTime()) ? null : day;
  }

  // The last moment to sign up and still get the ride; null without one.
  function perkCut(e) {
    if (!e.perkBy) return null;
    var cut = new Date(e.perkBy);
    return isNaN(cut.getTime()) ? null : cut;
  }

  // Calendar days from today: 0 today, 1 tomorrow, -1 yesterday.
  function daysUntil(d) {
    var now = new Date();
    var today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var day = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    return Math.round((day - today) / 86400000);
  }
  function daysLeft(n) {
    return n <= 0 ? "today" : n === 1 ? "tomorrow" : n + " days left";
  }
  function dayShort(d) {
    return d.toLocaleDateString([], {weekday:"short", month:"short", day:"numeric"});
  }

  // Every event still ahead that asks for a sign-up, with the moment to act
  // by: the stated deadline, the ride's cut-off or the event itself,
  // whichever comes first. Student posts never appear -- there is no
  // announcement to read a deadline from.
  function rsvpRows() {
    var now = Date.now();
    return EVENTS.filter(function (e) {
      return e.rsvpNeeded && when(e).getTime() > now;
    }).map(function (e) {
      var due = deadlineEnd(e), cut = perkCut(e);
      var closed = !!e.rsvpClosed || (!!due && due.getTime() < now);
      var times = [when(e).getTime()];
      if (due && !closed) times.push(due.getTime());
      if (cut && cut.getTime() > now) times.push(cut.getTime());
      else cut = null;
      return {e: e, due: due, cut: cut, closed: closed,
              track: e.track === "firm" ? "firm" : "school",
              act: new Date(Math.min.apply(null, times)),
              done: rsvped.has(String(e.id))};
    }).sort(function (a, b) { return a.act - b.act; });
  }

  function dueLine(r) {
    var e = r.e, line;
    if (r.closed) {
      line = '<div class="rdue none">' + (e.rsvpClosed && !(r.due && r.due < new Date())
        ? "Sign-ups have closed" : "RSVP closed " + esc(dayShort(r.due))) + "</div>";
    } else if (r.due) {
      var n = daysUntil(r.due);
      line = '<div class="rdue' + (n <= 1 ? " urgent" : "") + '">RSVP by ' +
        esc(dayShort(r.due)) + " · " + daysLeft(n) + "</div>";
    } else {
      // The date is on the line above; this says how long is left.
      var k = daysUntil(when(e));
      line = '<div class="rdue none">No deadline stated · event ' +
        (k <= 0 ? "today" : k === 1 ? "tomorrow" : "in " + k + " days") + "</div>";
    }
    if (r.cut && !r.closed) {
      var m = daysUntil(r.cut);
      var offer = (e.perks || []).filter(function (p) { return p.lead; })[0];
      line += '<div class="rdue perkdue' + (m <= 1 ? " urgent" : "") + '">' +
        esc(offer ? perkKind(offer.label) : "Perk") + " if you RSVP by " +
        esc(dayShort(r.cut) + ", " + fmtTime(r.cut)) + "</div>";
    }
    return line;
  }

  function rsvpRow(r) {
    var e = r.e, id = "rsvp-" + e.id;
    var meta = dayShort(when(e)) + (e.allDay ? "" : ", " + fmtSpan(e)) +
      (e.place ? " · " + e.place : "");
    var recep = recepTag(e);
    var chips = (recep ? '<span class="tag ' + recep[0] + '">' + esc(recep[1]) +
      "</span>" : "") + (e.perks || []).map(function (p) {
      return '<span class="tag perk">' + esc(perkText(p)) + "</span>";
    }).join("");
    return '<div class="rrow' + (r.done ? " done" : "") + '">' +
      '<input type="checkbox" id="' + esc(id) + '" data-rsvp="' + esc(e.id) +
        '"' + (r.done ? " checked" : "") +
        ' aria-label="' + esc("RSVP\u2019d: " + e.title) + '">' +
      "<div>" +
        '<div class="rtitle">' + esc(e.title) + "</div>" +
        '<div class="rmeta">' + esc(meta) + "</div>" +
        dueLine(r) +
        (chips ? '<div class="tags">' + chips + "</div>" : "") +
        (e.rsvp && /^https?:\/\//.test(e.rsvp)
          ? '<div class="racts"><a href="' + esc(e.rsvp) +
            '" target="_blank" rel="noopener noreferrer">RSVP \u2197</a></div>'
          : "") +
      "</div></div>";
  }

  var lastRsvps = "";
  function renderRsvps() {
    var rows = rsvpRows().filter(function (r) {
      return state.rshow !== "reception" || !!r.e.reception;
    });
    var counts = {firm: 0, school: 0}, next = null;
    rows.forEach(function (r) {
      if (r.done || r.closed) return;
      counts[r.track]++;
      // A date somebody wrote down, not the event's own start.
      [r.due, r.cut].forEach(function (d) {
        if (d && (!next || d < next)) next = d;
      });
    });
    var due = next ? "next deadline " + dayShort(next) : "";
    document.getElementById("rsvpSum").textContent =
      (state.rshow === "reception" ? "Receptions · " : "") +
      "Firms " + counts.firm + " · Law school " + counts.school +
      (due ? " · " + due : "");
    document.getElementById("rsvpSide").textContent = due;
    [].forEach.call(document.querySelectorAll("#rsvpTabs button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.track === state.track));
      b.querySelector(".n").textContent = counts[b.dataset.track];
    });
    [].forEach.call(document.querySelectorAll("#rsvpShow button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.rshow === state.rshow));
    });

    var groups = [["soon", "Due today or tomorrow", []], ["week", "This week", []],
                  ["later", "Later", []]];
    var closed = [], done = [];
    rows.forEach(function (r) {
      if (r.track !== state.track) return;
      if (r.done) return done.push(r);
      if (r.closed) return closed.push(r);
      var n = daysUntil(r.act);
      groups[n <= 1 ? 0 : n <= 7 ? 1 : 2][2].push(r);
    });
    var html = "";
    groups.forEach(function (g) {
      if (g[2].length) html += '<div class="rgroup ' + g[0] + '"><h3>' + g[1] +
        "</h3>" + g[2].map(rsvpRow).join("") + "</div>";
    });
    if (!html) html = '<p class="rempty">' + (done.length
      ? "Nothing left to RSVP for here."
      : state.rshow === "reception" ? "No receptions here ask for an RSVP."
      : "Nothing here asks for an RSVP.") + "</p>";
    [["closed", "RSVP closed", closed], ["done", "Done", done]].forEach(function (f) {
      if (f[2].length) html += '<details class="rfold" data-fold="' + f[0] + '"' +
        (state.folds[f[0]] ? " open" : "") + "><summary>" + f[1] + " (" +
        f[2].length + ")</summary>" + f[2].map(rsvpRow).join("") + "</details>";
    });
    // Redrawn every minute, so only when something changed: replacing the
    // rows would take the keyboard focus out of the list for nothing.
    if (html !== lastRsvps) {
      document.getElementById("rsvpList").innerHTML = html;
      lastRsvps = html;
    }
  }

  function why(e) {
    var rows = "";
    if (e.food && e.why) rows += "<dt>Food</dt><dd>" + esc(e.why) + "</dd>";
    if (e.evidence && e.evidence.length)
      rows += '<dt>Wording</dt><dd class="quote">' + e.evidence.map(function (p) {
        return "<span>" + esc(p) + "</span>"; }).join("") + "</dd>";
    if (e.net && e.netWhy) rows += "<dt>Room</dt><dd>" + esc(e.netWhy) + "</dd>";
    if (e.org) rows += "<dt>Host</dt><dd>" + esc(e.org) + "</dd>";
    // A food verdict a person supplied, with no phrase in any source to quote.
    // Said plainly, so a reader can weigh it against the quoted wording above.
    if (e.foodNote) rows += "<dt>Reported</dt><dd>" + esc(e.foodNote) + "</dd>";
    if (e.timeNote) rows += '<dt>Times</dt><dd class="dispute">' + esc(e.timeNote) + "</dd>";
    // The words the offer was read from, so a reader can weigh it.
    (e.perks || []).forEach(function (p) {
      rows += "<dt>Perk</dt><dd>" + esc(perkText(p)) +
        (p.phrase ? '<br><span class="quote"><span>' + esc(p.phrase) +
                    "</span></span>" : "") + "</dd>";
    });
    var cut = perkCut(e);
    if (cut) rows += "<dt>Cut-off</dt><dd>RSVP by " + esc(dayShort(cut) + ", " +
      fmtTime(cut)) + " to qualify</dd>";
    rows += "<dt>Source</dt><dd>" + esc(e.source || "Calendar") + "</dd>";
    if (e.flyer) {
      rows += "<dt>Flyer</dt><dd>" +
        (state.flyers ? "supplied \u2014 shown on the card"
                      : "supplied \u2014 shown below") + "</dd>";
    }
    // Shown here only when the page-wide toggle is off, so a flyer never
    // appears twice on the same card.
    var flyer = (e.flyer && !state.flyers)
      ? '<img class="flyer" src="' + esc(e.flyer) + '" loading="lazy" alt="Flyer for '
        + esc(e.title) + '">' : "";
    return '<div class="why"><dl>' + rows + "</dl>" + flyer + "</div>";
  }

  function entry(e) {
    var d = when(e);
    var open = state.open.has(e.id);
    return '<article class="entry" data-id="' + esc(e.id) + '">' +
      '<div class="rail">' + esc(fmtSpan(e)) + "</div><div>" +
      "<h4>" + esc(e.title) + "</h4>" +
      '<div class="where">' + (e.place
        ? '<span class="mono">' + esc(e.place) + "</span>" : "Location to be announced") +
        travel(e) +
        (e.org ? " &middot; " + esc(e.org) : "") +
        (e.submitted && e.by
          ? ' &middot; <span class="by">posted by ' + esc(e.by) + "</span>" : "") +
        "</div>" +
      '<div class="tags">' + tags(e) + "</div>" +
      (e.flyer && state.flyers ? '<img class="flyer" src="' + esc(e.flyer) +
        '" loading="lazy" alt="Flyer for ' + esc(e.title) + '">' : "") +
      '<div class="acts">' +
        '<button type="button" data-act="why" aria-expanded="' + open + '">' +
          (open ? "Hide detail" : "Why it is listed") + "</button>" +
        (e.rsvp && /^https?:\/\//.test(e.rsvp)
          ? '<a class="rsvp" href="' + esc(e.rsvp) + '" target="_blank" '
            + 'rel="noopener noreferrer">'
            + (e.submitted ? "See proof \u2197" : "RSVP") + "</a>" : "") +
        (e.cal && e.cal.google
          ? '<span class="addcal">Add to <a href="' + esc(e.cal.google) +
            '" target="_blank" rel="noopener noreferrer">Google Calendar</a> or <a href="' +
            esc(e.cal.outlook) + '" target="_blank" rel="noopener noreferrer">Outlook</a></span>'
          : "") +
      "</div>" + (open ? why(e) : "") + "</div></article>";
  }

  function renderLead(list) {
    var el = document.getElementById("lead"), e = list[0];
    if (!e) { el.innerHTML = ""; return; }
    var d = when(e), bits = [];
    if (e.place) bits.push(esc(e.place));
    bits.push(esc(dayLabel(d)) + (e.allDay ? "" : ", " + esc(fmtSpan(e))));
    el.innerHTML = '<div class="lead"><div class="when">Next up · ' +
      esc(countdown(d, e.allDay)) + '</div><h2>' + esc(e.title) + "</h2><p>" +
      bits.join(" &middot; ") + "</p></div>";
  }

  function renderList(list) {
    var host = document.getElementById("list");
    if (!list.length) {
      host.innerHTML = '<div class="empty"><strong>Nothing on the docket</strong>' +
        "Try the All lens, or check back after the next weekly broadcast.</div>";
      return;
    }
    var html = "", last = "";
    list.forEach(function (e) {
      var d = when(e), key = d.toDateString();
      if (key !== last) {
        html += '<div class="daybar"><h3>' + esc(dayLabel(d)) + '</h3><div class="fill"></div></div>';
        last = key;
      }
      html += entry(e);
    });
    host.innerHTML = html;
  }

  function renderFacets(pool) {
    // Built from the events themselves, so the menu shrinks as they age out
    // instead of accumulating options that match nothing. Counts come from
    // `pool` -- everything but the facet filter -- so ticking a box never
    // makes its own number disappear.
    var html = "", any = false;
    FACETS.forEach(function (f) {
      var counts = {};
      pool.forEach(function (e) {
        f[2](e).forEach(function (v) {
          if (v) counts[v] = (counts[v] || 0) + 1;
        });
      });
      var names = Object.keys(counts).sort(function (a, b) {
        return counts[b] - counts[a] || a.localeCompare(b);
      });
      if (!names.length) return;
      any = true;
      html += '<div class="facet"><h4>' + esc(f[1]) + "</h4><div class=\"opts\">" +
        names.map(function (v) {
          var on = state.facets[f[0]].has(v);
          return '<label class="opt' + (on ? " on" : "") + '">' +
            '<input type="checkbox" data-facet="' + esc(f[0]) + '" value="' +
            esc(v) + '"' + (on ? " checked" : "") + ">" +
            "<span>" + esc(v) + '</span><span class="n">' + counts[v] +
            "</span></label>";
        }).join("") + "</div></div>";
    });
    document.getElementById("facetBody").innerHTML = any
      ? html + '<button type="button" id="facetClear" class="quiet">Clear tags</button>'
      : '<p class="sheethint">Nothing to filter on yet.</p>';

    var n = facetCount();
    document.getElementById("facetLabel").textContent =
      n ? "Tags (" + n + ")" : "Tags";
    document.getElementById("facets").classList.toggle("active", n > 0);
  }

  function renderCal(list) {
    var host = document.getElementById("cal");
    if (!list.length) { host.innerHTML = '<div class="empty"><strong>Nothing scheduled</strong></div>'; return; }
    var byDay = {}, order = [];
    list.forEach(function (e) {
      var k = dayKey(when(e));
      // Collected in the order they are met, which `visible()` has already
      // sorted. Never sort these keys as strings: dayKey is not zero-padded,
      // so "2026-9-7" sorts after "2026-9-17" and Prev/Next walk backwards.
      if (!byDay[k]) { byDay[k] = []; order.push(k); }
      byDay[k].push(e);
    });

    var first = when(list[0]), last = when(list[list.length - 1]);
    var cursor = new Date(first.getFullYear(), first.getMonth(), 1);
    var stop = new Date(last.getFullYear(), last.getMonth(), 1);
    var today = new Date(), html = "";

    while (cursor <= stop) {
      var year = cursor.getFullYear(), month = cursor.getMonth();
      html += "<h3>" + cursor.toLocaleDateString([], {month:"long", year:"numeric"}) + "</h3>";
      html += '<div class="grid">' + DOW.map(function (d) {
        return '<div class="dow">' + d + "</div>"; }).join("");
      // Monday-first: JS getDay() is Sunday-first, so rotate it.
      var lead = (new Date(year, month, 1).getDay() + 6) % 7;
      for (var i = 0; i < lead; i++) html += '<div class="cell blank"></div>';
      var days = new Date(year, month + 1, 0).getDate();
      for (var day = 1; day <= days; day++) {
        var date = new Date(year, month, day);
        var found = byDay[dayKey(date)] || [];
        var cls = "cell" + (found.length ? " has" : "") +
          (date.toDateString() === today.toDateString() ? " today" : "") +
          (state.day === dayKey(date) ? " sel" : "");
        var key = dayKey(date);
        html += '<div class="' + cls + '" data-day="' + key + '"' +
          (found.length
            ? ' role="button" tabindex="0" aria-pressed="' +
              (state.day === key) + '" aria-label="' +
              esc(dayLabel(date) + ", " + found.length + " event" +
                  (found.length === 1 ? "" : "s")) + '"'
            : "") + ">" +
          '<div class="num">' + day + "</div>";
        found.slice(0, 3).forEach(function (e) {
          var at = when(e);
          // Both forms are rendered and CSS picks one, so a phone rotating to
          // landscape does not need a re-render to widen.
          html += '<div class="pip' + (!e.food && e.net ? " net" : "") + '">' +
            '<span class="at long">' +
              esc(e.allDay ? "all day" : fmtSpan(e)) + "</span>" +
            '<span class="at short">' +
              esc(e.allDay ? "all day" : fmtTimeShort(at)) + "</span>" +
            '<span class="what">' + esc(e.title) + "</span></div>";
        });
        if (found.length > 3) html += '<div class="more">+' + (found.length - 3) + "</div>";
        html += "</div>";
      }
      html += "</div>";
      cursor = new Date(year, month + 1, 1);
    }

    html += '<div class="picked">' + dayPanel(byDay, order) + "</div>";
    host.innerHTML = html;
  }

  function dayPanel(byDay, order) {
    if (!state.day) {
      return '<div class="hint">Pick a day to see what is on, ' +
             "with times and rooms.</div>";
    }
    var picked = byDay[state.day] || [];
    var at = order.indexOf(state.day);
    // Stepping moves between days that HAVE something on, not calendar days:
    // walking through four empty Tuesdays to reach the next event is not
    // looking at a specific day, it is paging through nothing.
    var prev = at > 0 ? order[at - 1] : "";
    var next = at >= 0 && at < order.length - 1 ? order[at + 1] : "";
    var when_ = new Date(picked.length ? picked[0].start : Date.now());

    return '<div class="pickbar">' +
      '<button type="button" data-step="' + esc(prev) + '"' +
        (prev ? "" : " disabled") + ' aria-label="Previous day with events">' +
        "&lsaquo; Prev</button>" +
      '<div class="pickday"><strong>' + esc(dayLabel(when_)) + "</strong>" +
        '<span class="count">' + picked.length + " event" +
        (picked.length === 1 ? "" : "s") + "</span></div>" +
      '<button type="button" data-step="' + esc(next) + '"' +
        (next ? "" : " disabled") + ' aria-label="Next day with events">' +
        "Next &rsaquo;</button>" +
      '<button type="button" data-step="" class="clear">Clear</button>' +
      "</div>" +
      (picked.length ? picked.map(entry).join("")
                     : '<div class="hint">Nothing that day.</div>');
  }

  function render() {
    var list = visible();
    renderFacets(beforeFacets());
    renderLead(list);
    renderRsvps();
    document.getElementById("count").textContent = EVENTS.length;
    var isList = state.view === "list";
    document.getElementById("list").hidden = !isList;
    document.getElementById("cal").hidden = isList;
    if (isList) renderList(list); else renderCal(list);
    [].forEach.call(document.querySelectorAll("#views button"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.view === state.view));
    });
    [].forEach.call(document.querySelectorAll("#lenses button[data-lens]"), function (b) {
      b.setAttribute("aria-pressed", String(b.dataset.lens === state.lens));
    });
    document.getElementById("flyerToggle")
            .setAttribute("aria-pressed", String(state.flyers));
    document.getElementById("postToggle")
            .setAttribute("aria-pressed", String(state.posts));
  }

  function fromB64(text) {
    var binary = atob(text);
    var bytes = Uint8Array.from(binary, function (c) { return c.charCodeAt(0); });
    return new TextDecoder().decode(bytes);
  }

  // Rebuild the whole document from the template the page carries, never from
  // the live DOM. String.replace with a plain string would also interpret $&
  // and friends inside the JSON, so every replacement is a function.
  // Placeholder names are assembled, never written out. Spelling one in full
  // anywhere in this script -- a comment included -- means the build
  // substitutes it here too, splicing the whole event feed into the code.
  function token(name) { return "__" + name + "__"; }

  function documentFor(state) {
    var tplB64 = document.getElementById("self-template").textContent.trim();
    return fromB64(tplB64)
      .replace(token("EVENTS"), function () { return JSON.stringify(EVENTS); })
      .replace(token("SUBMITTED"), function () {
        // Same reason as the build: a post whose title is a closing script
        // tag would end the data block early and take every other post with
        // it. The literal is not spelled out even here -- a comment inside a
        // script is still inside the script, and the HTML tokenizer ends the
        // element at the first such tag it sees, comment or not.
        return JSON.stringify(state).replace(/<\//g, "<\\/");
      })
      .replace(token("TEMPLATE"), function () { return tplB64; });
  }

  async function save(state, msgEl) {
    if (!writer) {
      if (msgEl) msgEl.textContent = "You do not have edit access to this page.";
      return false;
    }
    try {
      await writer.publish(documentFor(state));
      return true;
    } catch (err) {
      var code = err && err.code;
      // Reading the page and being able to change it are different grants,
      // and only the second one is testable -- by trying. So a reader who
      // cannot post is told once, plainly, and the control is taken away
      // rather than left there to fail again.
      // Every code the contract calls permanent for this view: the page has
      // no write path and never will on this load, so stop offering one.
      // `capability_disabled` is the one that matters for a shared link --
      // it is also how a host that cannot run the capability reports in.
      if (code === "not_writer" || code === "not_granted"
          || code === "not_declared" || code === "capability_disabled"
          || code === "capability_removed" || code === "consent_required") {
        writer = null;
        var bar = document.getElementById("submitBar");
        if (bar) bar.hidden = true;
        if (msgEl) msgEl.textContent =
          "Posting needs edit access to this page, which this link does not "
          + "grant. Ask whoever shared it with you.";
        return false;
      }
      // A conflict means another viewer published first; every view reloads
      // to the winner, so there is nothing useful to retry.
      if (msgEl) msgEl.textContent =
        (code === "conflict")
          ? "Someone else posted at the same moment. Try again."
          : "Could not save. Try again in a moment.";
      return false;
    }
  }

  function newId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  document.getElementById("doSubmit").addEventListener("click", async function () {
    var msg = document.getElementById("submitMsg");
    var by = document.getElementById("sName").value.trim();
    var title = document.getElementById("sTitle").value.trim();
    var when = document.getElementById("sWhen").value;
    if (by.length < 2) { msg.textContent = "Put your name on it."; return; }
    if (title.length < 3) { msg.textContent = "What is the event?"; return; }
    if (!when) { msg.textContent = "When is it?"; return; }
    if (blocked(by)) { msg.textContent = "That name has been blocked."; return; }

    var post = {
      id: newId(), by: by, title: title, start: when,
      place: document.getElementById("sPlace").value.trim(),
      note: document.getElementById("sNote").value.trim(),
      link: document.getElementById("sLink").value.trim(),
      at: new Date().toISOString()
    };
    if (post.link && !/^https?:\/\//.test(post.link)) post.link = "";

    this.disabled = true;
    msg.textContent = "Posting\u2026";
    var next = {posts: SUB.posts.concat([post]), blocked: SUB.blocked};
    var ok = await save(next, msg);
    this.disabled = false;
    if (ok) {
      SUB = next;
      document.getElementById("submitSheet").close();
      ["sTitle", "sWhen", "sPlace", "sNote", "sLink"].forEach(function (id) {
        document.getElementById(id).value = "";
      });
      render();
    }
  });

  function renderModeration() {
    var host = document.getElementById("modList");
    var counts = {};
    SUB.posts.forEach(function (p) {
      var key = String(p.by || "someone").trim();
      counts[key] = (counts[key] || 0) + 1;
    });
    var names = Object.keys(counts).sort();
    if (!names.length && !SUB.blocked.length) {
      host.innerHTML = '<p class="sheethint">Nothing has been submitted yet.</p>';
      return;
    }
    host.innerHTML = names.map(function (name) {
      return '<div class="modrow"><span>' + esc(name) + " \u00b7 " +
        counts[name] + (counts[name] === 1 ? " post" : " posts") +
        '</span><button type="button" data-purge="' + esc(name) +
        '">Remove all</button></div>';
    }).join("") + (SUB.blocked.length
      ? '<p class="sheethint" style="margin-top:14px">Blocked: ' +
        esc(SUB.blocked.join(", ")) + "</p>" : "");
  }

  document.getElementById("modList").addEventListener("click", async function (ev) {
    var btn = ev.target.closest("button[data-purge]");
    if (!btn) return;
    var name = btn.dataset.purge;
    if (!window.confirm("Remove every post by " + name + " and block the name?")) return;
    btn.disabled = true;
    var key = name.trim().toLowerCase();
    var next = {
      posts: SUB.posts.filter(function (p) {
        return String(p.by || "").trim().toLowerCase() !== key;
      }),
      blocked: SUB.blocked.concat(SUB.blocked.indexOf(key) < 0 ? [key] : [])
    };
    if (await save(next)) { SUB = next; renderModeration(); render(); }
    btn.disabled = false;
  });

  document.getElementById("openSubmit").addEventListener("click", function () {
    document.getElementById("submitMsg").textContent = "";
    document.getElementById("submitSheet").showModal();
  });
  document.getElementById("openModerate").addEventListener("click", function () {
    renderModeration();
    document.getElementById("moderateSheet").showModal();
  });

  // Submitting needs edit access, which `use()` does not report: it resolves
  // for readers too, and only `publish` rejects. So the controls appear for
  // anyone the capability loads for, and `save` takes them away again the
  // first time a reader finds out they cannot post.
  if (window.claude && window.claude.use) {
    window.claude.use("artifact").then(function (api) {
      writer = api;
      if (api) document.getElementById("submitBar").hidden = false;
    }).catch(function () {});
  }

  function pickDay(key) {
    state.day = state.day === key ? null : key;
    render();
    if (state.day) revealPanel();
  }

  function revealPanel() {
    // On a phone the grid fills the screen and the answer lands below the
    // fold, so selecting a day looks like it did nothing at all.
    var panel = document.querySelector(".picked");
    if (!panel || !panel.scrollIntoView) return;
    var still = window.matchMedia
      && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    try {
      panel.scrollIntoView({block: "nearest",
                            behavior: still ? "auto" : "smooth"});
    } catch (e) { panel.scrollIntoView(); }
  }

  // A cell that behaves like a button has to answer the keyboard like one.
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Enter" && ev.key !== " " && ev.key !== "Spacebar") return;
    var cell = ev.target.closest && ev.target.closest(".cell.has");
    if (!cell) return;
    ev.preventDefault();
    pickDay(cell.dataset.day);
  });

  document.getElementById("views").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-view]");
    if (!b) return;
    state.view = b.dataset.view; write(KEY_VIEW, state.view); render();
  });
  document.getElementById("lenses").addEventListener("click", function (ev) {
    if (ev.target.id === "flyerToggle") {
      state.flyers = !state.flyers;
      write(KEY_FLYERS, state.flyers ? "1" : "0");
      return render();
    }
    if (ev.target.id === "postToggle") {
      state.posts = !state.posts;
      write(KEY_POSTS, state.posts ? "1" : "0");
      return render();
    }
    var b = ev.target.closest("button[data-lens]");
    if (!b) return;
    state.lens = b.dataset.lens; write(KEY_LENS, state.lens); render();
  });

  // Open on a first visit, because the page is no use if nobody knows what
  // the tags mean. Shut once and it stays shut.
  (function () {
    var about = document.getElementById("about");
    if (!about) return;
    if (read(KEY_ABOUT, "1") !== "1") about.open = false;
    about.addEventListener("toggle", function () {
      write(KEY_ABOUT, about.open ? "1" : "0");
    });
  })();

  document.getElementById("facets").addEventListener("change", function (ev) {
    var box = ev.target.closest('input[data-facet]');
    if (!box) return;
    var picked = state.facets[box.dataset.facet];
    if (box.checked) picked.add(box.value); else picked.delete(box.value);
    writeFacets();
    render();
  });

  document.getElementById("facets").addEventListener("click", function (ev) {
    if (!ev.target.closest("#facetClear")) return;
    FACETS.forEach(function (f) { state.facets[f[0]].clear(); });
    writeFacets();
    render();
  });

  document.addEventListener("click", function (ev) {
    if (ev.target.classList && ev.target.classList.contains("flyer")) {
      ev.stopPropagation();
      ev.target.classList.toggle("zoom");   // tap the flyer to read it full-screen
      return;
    }
    var step = ev.target.closest("button[data-step]");
    if (step) {
      state.day = step.dataset.step || null;
      render();
      if (state.day) revealPanel();
      return;
    }
    var cell = ev.target.closest(".cell.has");
    if (cell) {
      pickDay(cell.dataset.day);
      return;
    }
    var btn = ev.target.closest('button[data-act="why"]');
    if (!btn) return;
    var id = ev.target.closest(".entry").dataset.id;
    if (state.open.has(id)) state.open.delete(id); else state.open.add(id);
    render();
  });

  // The tracker: its tabs, the ticks, and the folded groups.
  document.getElementById("rsvpTabs").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-track]");
    if (!b) return;
    state.track = b.dataset.track; write(KEY_RSVP_TAB, state.track);
    renderRsvps();
  });
  document.getElementById("rsvpShow").addEventListener("click", function (ev) {
    var b = ev.target.closest("button[data-rshow]");
    if (!b) return;
    state.rshow = b.dataset.rshow; write(KEY_RSVP_SHOW, state.rshow);
    renderRsvps();
  });
  document.getElementById("rsvpList").addEventListener("change", function (ev) {
    var box = ev.target.closest("input[data-rsvp]");
    if (!box) return;
    // Ticking moves the row to Done, so keep the keyboard where it was: on
    // the tick that now sits in the ticked row's old place.
    var boxes = [].slice.call(document.querySelectorAll("#rsvpList input[data-rsvp]"));
    var at = boxes.indexOf(box);
    if (box.checked) rsvped.add(box.dataset.rsvp); else rsvped.delete(box.dataset.rsvp);
    writeTicks();
    renderRsvps();
    var after = document.querySelectorAll("#rsvpList input[data-rsvp]");
    var land = after[Math.min(at, after.length - 1)];
    if (land && land.offsetParent !== null) land.focus();
  });
  // `toggle` does not bubble, so it is caught on the way down.
  document.getElementById("rsvpList").addEventListener("toggle", function (ev) {
    var fold = ev.target;
    if (fold.matches && fold.matches("details.rfold") && fold.dataset.fold) {
      state.folds[fold.dataset.fold] = fold.open;
    }
  }, true);

  // A folding panel under the "Next up" card, closed until opened, and
  // remembered. Only a narrow screen reads this: beside the docket, the
  // stylesheet shows the list whatever the panel was left as.
  (function () {
    var box = document.getElementById("rsvps");
    var head = document.getElementById("rsvpToggle");
    function show(open) {
      box.classList.toggle("open", open);
      head.setAttribute("aria-expanded", String(open));
    }
    show(read(KEY_RSVP_OPEN, "0") === "1");
    head.addEventListener("click", function () {
      var open = !box.classList.contains("open");
      show(open);
      write(KEY_RSVP_OPEN, open ? "1" : "0");
    });
  })();

  render();
  setInterval(function () { renderLead(visible()); renderRsvps(); }, 60000);
})();
</script>
"""


def carried_submissions(path: str = None, verdicts: dict = None) -> dict:
    """Read the submissions out of a previously published page.

    The scheduled rebuild saves the live page here first, so posts survive a
    republish. Missing or unreadable means start empty rather than fail.

    `verdicts` is what the review pass decided, keyed on post id. A post that
    has been **verified** is already in the database as a scored event, so
    carrying it here too would list it twice; a **rejected** one should stop
    appearing. Anything unreviewed is carried through untouched, which is what
    lets a post go up the moment it is made.
    """
    if not path or not os.path.exists(path):
        return {"posts": [], "blocked": []}
    try:
        with open(path, encoding="utf-8") as fh:
            html = fh.read()
    except OSError:
        return {"posts": [], "blocked": []}
    marker = '<script type="application/json" id="submitted-data">'
    start = html.find(marker)
    if start < 0:
        return {"posts": [], "blocked": []}
    start += len(marker)
    end = html.find("</script>", start)
    try:
        state = json.loads(html[start:end])
    except ValueError:
        return {"posts": [], "blocked": []}
    if not isinstance(state, dict) or not isinstance(state.get("posts"), list):
        return {"posts": [], "blocked": []}
    state.setdefault("blocked", [])
    if verdicts:
        state["posts"] = [
            p for p in state["posts"]
            if (verdicts.get(str(p.get("id")), {}) or {}).get("verdict", "")
            not in ("verified", "rejected")]
    return state


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "data", "page.html")
    conn = store.connect()
    carried = carried_submissions(sys.argv[2] if len(sys.argv) > 2 else None,
                                  store.submission_verdicts(conn))
    conn = store.connect()
    built = build(conn, submitted=carried)
    conn.close()
    with open(out, "w") as fh:
        fh.write(built["html"])
    print(json.dumps({"page": out, "bytes": built["bytes"],
                      "events": len(built["events"]),
                      "files": len(built["files"]),
                      "carried_submissions": len(carried["posts"])}, indent=1))
    manifest = os.path.join(os.path.dirname(out), "page-files.json")
    with open(manifest, "w") as fh:
        json.dump(built["files"], fh, indent=1)
    print("manifest:", manifest)


if __name__ == "__main__":
    main()
