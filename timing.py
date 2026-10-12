"""When an event actually runs, according to each source that said so.

Three things can state a time, and they do not always agree:

* the **flyer graphic**, read by OCR -- big display type, usually the range
  the organiser designed the poster around ("6:00-7:00PM");
* the **email text** around the flyer, typed by a person into the broadcast
  ("APALSA First Look, September 17 2026, 5:30-7:00PM, McDonough Atrium");
* whatever is **published**, which `caption.ground` derived from those two by
  a rule that prefers the typed caption, on the reasoning that typing is more
  reliable than photographing.

That rule is right more often than not -- most disagreements here are OCR
picking "6 PM" out of "SAT 10 AM - 6 PM" and calling it the start. But it is
not right always, and when it is wrong the reader turns up half an hour late
to an event that has already started handing out the food.

So this module does not decide anything. It lays the three readings side by
side, says which disagree and by how much, and leaves the judgement to a
person -- who can then record it with `store.record_correction`, which
outranks all three.

The second thing it tracks is **end times**. A start alone answers "can I get
there"; it does not answer "is it worth leaving now", which is the question a
reader with a 4:45 class actually has. An event with no end is not wrong, but
it is incomplete, and worth reporting as such.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import caption as captions
import dateparse
import store

# Below this the two readings are the same event, differently rounded -- a
# caption saying 6:00 against a flyer saying 6:00 is agreement even when one
# carried seconds. Above it they are two different claims about the world.
SAME_MINUTES = 1

# A gap this wide is almost always OCR grabbing the END time and reporting it
# as the start ("SAT 10 AM - 6 PM" read as 18:00). Worth saying out loud in
# the report, because it is the one disagreement with a known cause.
LIKELY_END_AS_START = timedelta(hours=3)

# Reading a time off a poster is guesswork below this, and a low-confidence
# guess dressed up as a second opinion is worse than no second opinion: it
# manufactures disagreements a person then has to adjudicate.
OCR_MIN_CONFIDENCE = 70


def _at(value: str):
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _clock(value: str) -> str:
    moment = _at(value)
    return moment.strftime("%H:%M") if moment else ""


def _span(start: str, end: str) -> str:
    """"5:30pm-7:00pm", or just the start when nothing says when it ends."""
    first, last = _at(start), _at(end)
    if not first:
        return "--"
    text = first.strftime("%-I:%M%p").lower()
    if last and last > first:
        return text + "-" + last.strftime("%-I:%M%p").lower()
    return text


def _stated(said: dict) -> bool:
    """Whether a caption reading actually named a clock time."""
    return bool(said.get("starts_at")) and not said.get("all_day") \
        and _clock(said["starts_at"]) != "00:00"


def readings(conn, event) -> dict:
    """What each source says about one event's start and end.

    Returns `published`, `flyer` and `caption`, each `{"start", "end"}` with
    empty strings where that source said nothing.
    """
    event = dict(event)
    out = {
        "id": event.get("id") or "",
        "title": event.get("title") or "",
        "all_day": bool(event.get("all_day")),
        "published": {"start": event.get("starts_at") or "",
                      "end": event.get("ends_at") or ""},
        "flyer": {"start": "", "end": ""},
        "caption": {"start": "", "end": ""},
        "flyer_id": "",
        "flyer_from": "",
        "shared_flyer": False,
    }
    row = conn.execute(
        "SELECT id, reading, caption, week_of, ocr_text FROM flyers "
        "WHERE event_id = ? ORDER BY rowid LIMIT 1",
        (event.get("id"),)).fetchone()
    if row is None:
        return out
    out["flyer_id"] = row["id"]

    # One poster advertising a week of events carries one `reading`, and there
    # is no way to tell which of its events that reading describes. Comparing
    # the other events against it invents disagreements: SIPLA's Patent Week
    # flyer made its Monday event look 27 hours out from its own artwork.
    shared = conn.execute(
        "SELECT COUNT(*) n FROM events WHERE flyer_id = ?",
        (row["id"],)).fetchone()["n"]
    out["shared_flyer"] = shared > 1

    # What the artwork says, in two layers. `reading` is the transcription
    # made when someone looked at the poster, and is the better answer -- but
    # only 12 of 36 attached flyers have one, because it is written by a step
    # that does not run on every flyer. The OCR text is always there, so it is
    # the fallback: less tidy, but it is literally what is printed.
    try:
        read = json.loads(row["reading"] or "{}")
    except ValueError:
        read = {}
    if isinstance(read, dict) and read.get("starts_at"):
        out["flyer"] = {"start": read.get("starts_at") or "",
                        "end": read.get("ends_at") or ""}
        out["flyer_from"] = "transcription"
    elif (row["ocr_text"] or "").strip():
        anchor = _at(row["week_of"] or "")
        start, end, all_day, confidence = dateparse.find_event_datetime(
            row["ocr_text"], anchor)
        if start and not all_day and confidence >= OCR_MIN_CONFIDENCE:
            out["flyer"] = {
                "start": start.isoformat(timespec="seconds"),
                "end": end.isoformat(timespec="seconds") if end else ""}
            out["flyer_from"] = "OCR"

    # What the organiser typed underneath it.
    if (row["caption"] or "").strip():
        anchor = _at(row["week_of"] or "")
        said = captions.read(row["caption"], anchor)
        if _stated(said):
            out["caption"] = {"start": said.get("starts_at") or "",
                              "end": said.get("ends_at") or ""}
    return out


def assess(conn, event) -> dict:
    """The three readings, what disagrees, and what is missing.

    `agree` is False only when two sources both named a time and named
    different ones. A source that said nothing is silent, not dissenting.
    """
    found = readings(conn, event)
    pub, flyer, cap = found["published"], found["flyer"], found["caption"]
    issues, notes = [], []

    def differ(a: str, b: str) -> bool:
        """Whether two readings name different clock times on the same day.

        Same day is part of the question, not a detail. Two readings on
        different dates are two different events -- on a shared flyer, the
        usual case -- and calling that a half-hour dispute is nonsense.
        """
        first, second = _at(a), _at(b)
        if not first or not second or first.date() != second.date():
            return False
        return abs(first - second) > timedelta(minutes=SAME_MINUTES)

    if found["all_day"]:
        # Nothing stated an hour, so there is no clock to check and no end to
        # be missing. Saying "12:00am" of an all-day event is worse than
        # saying nothing.
        found.update({"issues": [], "notes": ["listed as all day; no times stated"],
                      "ruled": [], "agree": True, "span": "all day"})
        return found

    if found["shared_flyer"]:
        flyer = {"start": "", "end": ""}

    if differ(flyer["start"], cap["start"]):
        gap = abs(_at(flyer["start"]) - _at(cap["start"]))
        issues.append(
            "start: the flyer reads %s, the email says %s"
            % (_span(flyer["start"], ""), _span(cap["start"], "")))
        if gap >= LIKELY_END_AS_START:
            notes.append("%s apart -- wide enough that OCR most likely took "
                         "the flyer's END time for its start"
                         % _humanise_gap(gap))
    if differ(flyer["end"], cap["end"]):
        issues.append(
            "end: the flyer reads %s, the email says %s"
            % (_span(flyer["end"], ""), _span(cap["end"], "")))

    # Whatever the sources said, the reader sees the published row. If that
    # matches neither source, something wrote it that is not in this picture.
    sources = [s for s in (flyer["start"], cap["start"]) if s]
    if sources and pub["start"] and all(differ(pub["start"], s) for s in sources):
        issues.append("published %s matches neither source"
                      % _span(pub["start"], ""))

    if not pub["end"]:
        offered = flyer["end"] or cap["end"]
        if offered:
            issues.append("no end time published, though the %s gives one (%s)"
                          % ("flyer" if flyer["end"] else "email",
                             _span(offered, "")))
        else:
            notes.append("no end time in any source")

    ruled = store.corrected_fields(conn, found["id"])
    found.update({
        "issues": issues,
        "notes": notes,
        "ruled": sorted(ruled & {"starts_at", "ends_at"}),
        "agree": not issues,
        "span": _span(pub["start"], pub["end"]),
    })
    return found


def _humanise_gap(gap: timedelta) -> str:
    minutes = int(gap.total_seconds() // 60)
    hours, rest = divmod(minutes, 60)
    if hours and rest:
        return "%dh %dm" % (hours, rest)
    if hours:
        return "%d hour%s" % (hours, "s" if hours > 1 else "")
    return "%d min" % rest


def sweep(conn, events) -> list:
    """Every event whose timing is worth a second look, worst first.

    A disagreement between two sources outranks a merely missing end time:
    one means the page may be wrong, the other only that it is incomplete.
    """
    out = [assess(conn, e) for e in events]
    flagged = [a for a in out if a["issues"] or a["notes"]]
    flagged.sort(key=lambda a: (not a["issues"], a["published"]["start"] or ""))
    return flagged


def note_for(assessment: dict) -> str:
    """One line for the reader, when the sources genuinely do not agree.

    Written to be read on the event card by someone deciding when to leave, so
    it names both times rather than announcing that a discrepancy exists.

    Two things keep it quiet, and both matter more than the coverage lost:

    * **A ruling settles it.** Once a person has said which time is right,
      there is nothing left for the reader to adjudicate.
    * **OCR alone is not a second opinion.** Reading "SAT 10 AM - 6 PM" off
      artwork produced a start of 6:00pm for the ADR Bootcamp -- telling a
      reader "the flyer reads 6:00pm" would repeat a parsing bug as though it
      were printed on the wall. Only a transcription, where the poster was
      actually read, is quoted back to readers; an OCR guess stays in
      `./fff times` for a person to judge.
    """
    if assessment.get("ruled") or not assessment.get("issues"):
        return ""
    if assessment.get("flyer_from") != "transcription":
        return ""
    flyer = assessment["flyer"]["start"]
    cap = assessment["caption"]["start"]
    if not flyer or not cap or _clock(flyer) == _clock(cap):
        return ""
    return ("the flyer reads %s and the email says %s — check the flyer"
            % (_span(flyer, ""), _span(cap, "")))
