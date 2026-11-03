"""Georgetown University Law Center, on the Capitol Campus.

The first profile, and the one every rule in this project was learned on. The
comments say which event or email forced each line; keep that habit when you
add to it, because a pattern with no reason attached is one nobody dares to
change.
"""

NAME = "Georgetown Law"
CAMPUS = "Capitol Campus"
TIMEZONE = "America/New_York"

# --- mail ------------------------------------------------------------------

DOMAINS = ("georgetown.edu",)

# A Georgetown netID always carries digits -- zzz65, zz2152, zz155, zz1538.
# Role accounts never do: lawsba, cimgmt, cflo, opics, blsa, fedsoc, ocs, els.
# That one character class separates a classmate from a student org across
# every sender this mailbox has seen, and it needs no list to maintain, which
# matters because the orgs change every year and a stale allowlist fails by
# dropping real events silently.
PERSON = r"\d"

# Outbound broadcast platforms. Mail from these is announcements by
# construction: you cannot send a personal note through a mail-merge system.
# The Dean's office mails the community as a netID at info.georgetown.edu, and
# that is still a broadcast.
BROADCAST_DOMAINS = (
    "info.georgetown.edu",
    "campusgroups.georgetown.edu",
    "lists.georgetown.edu",
    "mail.campusgroups.com",
    # The platform's own mailbox, not the school's relay. Membership notices
    # come from admin@campusgroups.com while the orgs' newsletters relay
    # through campusgroups.georgetown.edu, so without this the confirmations
    # are refused and an org that mails direct from the platform is missed.
    "campusgroups.com",
)

# Student-body lists. A message addressed to one of these is, by definition,
# addressed to everybody.
LISTSERVS = (r"^(?:lawstudentsall|lawstudents|lawfacultyandstaff|allstudents|"
             r"law[a-z]*all|[a-z]+students|55hst)@")

# Firms write to the 1L class from their own domains.
LAW_FIRM_MAIL = True

# The school's own click-tracking hosts. The `qs=` token on each link is
# per-recipient: following one registers a click under the reader's name.
TRACKING_HOSTS = (
    "click.info.georgetown.edu",
    "click.e.georgetown.edu",
    "email.georgetown.edu",
)

# CampusGroups relays every org through one address; the org's name sits
# above this line in the footer.
CAMPUSGROUPS_FOOTER = "Georgetown University"

# --- the OSL Weekly Broadcast ------------------------------------------------

# The Office of Student Life's weekly mailing carries ~78 event flyers as
# images and next to nothing as text. ./fff broadcast reads the pictures.
FLYERS = {
    "label": "OSL flyer",
    "description": "the Office of Student Life weekly broadcast",
    "senders": ("lawstudentlife@", "osl@", "studentlife@"),
    "subjects": ("weekly broadcast", "osl weekly", "student life weekly"),
    # Letterhead on nearly every flyer, and never its title.
    "letterhead": (
        "georgetown law", "office of student life", "georgetown university",
        "law center", "approved by", "approved hy", "gulc",
        "georgetown university law center",
    ),
}

# "Approved by Georgetown Law" is stamped on every OSL flyer, wherever the
# event is. It names who approved the flyer, not the room, and it put OutLaw's
# happy hour at The Dubliner, and Howdy Hoyas' coffee at Tatte, "at
# Georgetown Law".
STAMP = r"\bapproved\s+by\s+(?:the\s+)?(?:georgetown\s+law|law\s+center|gulc)\b"

# --- the campus ----------------------------------------------------------------

# (name pattern, full name, short name for display beside a room number)
# Patterns match the BUILDING only. Room numbers are pulled out separately,
# so "MCD 208" resolves to McDonough plus room 208 rather than being swallowed
# whole by the venue pattern.
VENUES = (
    (r"mcdonough|mcdonaugh|\bmcd\b\.?|600\s+new\s+jersey|hart\s+auditorium|"
     r"the\s+whisk", "McDonough Hall", "McDonough"),
    (r"hotung|550\s+(?:first|1st)\s+st", "Hotung", "Hotung"),
    (r"williams\s+(?:law\s+)?library|edward\s+bennett\s+williams|111\s+g\s+st",
     "Williams Library", "Williams Library"),
    (r"gewirz|120\s+f\s+st", "Gewirz Student Center", "Gewirz"),
    (r"sport\s*(?:&|and)\s*fitness|111\s+f\s+st", "Sport & Fitness Center",
     "Sport & Fitness"),
    (r"500\s+(?:first|1st)\s+st", "500 First Street NW", "500 First St NW"),
    # Every way organisers write it: "111 Massachusetts Ave NW", "111 Mass
    # Ave", "Mass 111", "111 Mass Lobby". Only the full spelling was known, so
    # the short ones fell through to the street-address rule and a Capitol
    # Campus building was labelled "Off campus". Anchored, or "1111
    # Massachusetts Ave" would be ours too.
    (r"\b111\s+mass(?:achusetts)?\b\.?(?:\s+ave(?:nue)?\b\.?)?(?:\s*,?\s*nw\b\.?)?|"
     r"\bmass(?:achusetts)?\.?\s+(?:ave(?:nue)?\.?\s+)?111\b",
     "111 Massachusetts Ave NW", "111 Mass Ave NW"),
    (r"mccourt|125\s+e\s+st", "McCourt School", "McCourt"),
    # The lawn outside McDonough, where the picnics happen. Unlisted, it fell
    # through to the branding in the flyer's own text and was published as
    # "Georgetown Law" -- a building, for an event on the grass.
    (r"eleanor\s+holmes\s+norton\s+green|\bnorton\s+green\b",
     "Eleanor Holmes Norton Green", "Norton Green"),
    (r"capitol\s+campus", "Capitol Campus", "Capitol Campus"),
    (r"georgetown\s+law|law\s+center|\bgulc\b", "Georgetown Law", "Georgetown Law"),
)

# The entry above that is only the school's name. In an event's own location
# it is a statement of where; in the blurb beside a named location it is the
# institution, and "Georgetown Law 401" was published for a panel at the
# Martin Luther King Jr. Memorial Library, Room 401-A.
SCHOOL_VENUE = "Georgetown Law"

# Main Campus, in Burleith, 4km away. Not walkable, and not what the docket is
# for. "Healey Family Student Center" is how the feed spells it -- one letter
# from "Healy Hall", and the reason that whole building slipped through.
AWAY = (r"heal(?:e)?y|\bhfsc\b|\bosei\b|art\s+gallery|keough|davis\s+center|"
        r"\bicc\b|intercultural\s+center|regents?\s+hall|lauinger|konrad|"
        r"white\s+gravenor|hariri|lohrfink|copley|dahlgren|leavey|village\s+c|"
        r"yates|mortara|car\s+barn|walsh|poulton|st\.?\s+mary|main\s+campus|"
        r"reiss|maguire|red\s+square|bulldog\s+tavern|arrupe|darnall|harbin|"
        r"kennedy\s+hall|epicurean|riggs\s+library|fisher\s+colloquium|"
        r"37th\s+and\s+o|3700\s+o\s+st")
AWAY_NAME = "Main Campus"
AWAY_TRAVEL = "25-min shuttle"

# LiveWhale groups that only ever publish Capitol Campus events.
HOME_GROUPS = ("law", "studentaffairscapitolcampus", "capitolcampus",
               "mccourt", "gulc")

# Named spaces inside a building. The Whisk is the part you actually walk to,
# and reads "The Whisk, McDonough", not "McDonough The Whisk".
SPACES = (r"the\s+whisk", r"hart\s+auditorium", r"moot\s+court(?:\s+room)?",
          r"gewirz\s+lounge")
PROPER_SPACES = (r"the\s+whisk", r"hart\s+auditorium", r"moot\s+court")

# Organisers write the building and the room as one token -- "McD520",
# "Hotung1000". Without a space the room pattern's word boundary never fires
# and the room is lost, so the event reads "McDonough" with no number on it.
ROOM_PREFIXES = ("mcd", "mcdonough", "hotung", "gewirz", "mccourt")

# Where an email's own text says the event is.
MAIL_ROOM = (r"\b(?:in|at|room|rm\.?)\s+((?:McDonough|Hotung|Gewirz|Williams|"
             r"Hart|Sport)\b[^.,;\n]{0,40}"
             r"|\d{3,4}[A-Z]?\b(?:\s+(?:McDonough|Hotung|Gewirz))?)")
# MCD/MCD. is how flyers abbreviate McDonough, and the room number is the
# useful half ("MCD 208").
MAIL_VENUE = (r"\b(McDonough(?:\s+Hall)?(?:\s+\d{3,4})?|MCD\.?\s*\d{3,4}|"
              r"Hotung(?:\s+\d+)?|Gewirz(?:\s+\w+)?|The\s+Whisk|"
              r"Hart\s+Auditorium|Williams\s+Library|Sport\s*(?:&|and)\s*Fitness)\b")

# The law school's own buildings, as the campus code names them. An event
# held in one is a law-school event whatever its blurb says: Georgetown Youth
# Advocates' career fair lists "juvenile justice, youth defense, child
# welfare" and never the word "law", so a career fair in the McDonough Atrium
# was scored as having nothing legal about it. The Capitol Campus test is what
# keeps the business school's McDonough out.
LAW_BUILDINGS = ("McDonough", "Hotung", "Gewirz", "Sport & Fitness", "Williams")
LAW_GROUPS = ("law", "studentaffairscapitolcampus")

# --- the events calendar ------------------------------------------------------

# events.georgetown.edu runs LiveWhale, which publishes a university-wide iCal
# feed. Most of it is Main Campus; the campus rules sort it.
CALENDAR = "https://events.georgetown.edu/live/ical/events?days={days}"
CALENDAR_NAME = "Georgetown&rsquo;s public events calendar"
CALENDAR_GROUP = r"https://events\.georgetown\.edu/([^/]+)/"
# Group slugs are machine names ("studentaffairscapitolcampus"). Show a
# readable name where we know one; otherwise show nothing, because an
# unsplittable slug tells the reader less than blank space does.
CALENDAR_GROUP_NAMES = {
    "law": "Georgetown Law", "studentaffairscapitolcampus": "Student Affairs",
    "capitolcampus": "Capitol Campus", "mccourt": "McCourt School",
    "campusministry": "Campus Ministry", "registrar": "Registrar",
    "live": "",
}

# --- words -------------------------------------------------------------------

# "Georgetown Law X" is X; the campus is a given on this page. OCR reads a
# flyer's whole header as the org, and "Georgetown Law Barrister's Council
# Alternative Dispute Resolution" is not what anyone calls them.
ORG_PREFIX = r"^georgetown\s+(?:university\s+)?(?:law\s+)?(?:center\s+)?"
GENERIC_ORGS = ("georgetown law", "the law center", "law center")

# Buildings, neighbourhoods, people and student orgs a dictionary has never
# heard of. Never misspellings.
PROPER_NOUNS = (
    "georgetown", "gulc", "hoya", "hoyas", "mcdonough", "hotung", "gewirz",
    "lauinger", "burleith", "takoma", "dupont", "ginsburg", "buzbee",
    "brazla", "sipla", "glels", "giels", "gema", "gias", "ipip", "kalsa",
    "calsa", "dlsa", "cflo", "osl",
)

COMMON_WORDS = ("georgetown", "law")
LINK_DOMAINS = ("georgetown.edu", "law.georgetown.edu")
ROOM_EXAMPLE = "McDonough 208"
