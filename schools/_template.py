"""A profile for your school: copy this file, fill it in, and choose it.

    cp schools/_template.py schools/state_law.py
    # data/config.json:  {"school": "state_law"}
    ./fff school        # says what the docket now believes about your school

Every setting below starts empty, and empty means "refuse" or "do not know":
a copy of this file, unedited, behaves exactly like no profile at all. Fill in
what you can vouch for, and leave the rest. schools/georgetown_law.py is a
complete example; read its comments for why each rule exists.

Patterns are Python regular expressions, matched without regard to case.
Write them as raw strings: r"\\d", not "\\\\d".

The one setting to get right before any other is PERSON. It is what stops a
classmate's email being published as if an organisation had sent it. Look at
twenty senders in your inbox -- classmates, offices, student orgs -- and find
what always marks a person's address and never an office's. If nothing does,
leave PERSON empty and add organisations one at a time with
`./fff sources add <address>`.
"""

# --- the school -------------------------------------------------------------

NAME = ""                 # How the page names it: "Georgetown Law".
CAMPUS = ""               # The campus this docket covers: "Capitol Campus".
TIMEZONE = ""             # For the calendar feed: "America/New_York".

# --- mail: who counts as an organisation -------------------------------------

DOMAINS = ()              # Your school's mail domains: ("example.edu",).
                          # Subdomains count, so law.example.edu is included.
PERSON = ""               # Found in the local part (before the @) of every
                          # person's address and no office's. Georgetown's
                          # netIDs always carry a digit: r"\d". Where people
                          # are first.last@: r"\.".
BROADCAST_DOMAINS = ()    # Mail-merge platforms that send nothing personal:
                          # ("info.example.edu", "example.campusgroups.com").
LISTSERVS = ""            # Lists that reach every student, as a pattern on
                          # the recipient: r"^(?:allstudents|lawstudents)@".
LAW_FIRM_MAIL = False     # True to admit a law firm's recruiting mail sent to
                          # the whole class (never one written to you alone).
TRACKING_HOSTS = ()       # Your school's click-tracking hosts, whose links
                          # name the reader: ("click.info.example.edu",).
CAMPUSGROUPS_FOOTER = ""  # If your orgs mail through CampusGroups: the line
                          # under the org's name in the footer, usually the
                          # university's name.

# --- a newsletter of flyers -------------------------------------------------

# If your student-life office mails a weekly newsletter of event flyers as
# images, ./fff broadcast reads them (macOS only: it uses the system's OCR).
#
# FLYERS = {
#     "label": "SLO flyer",          # the source, as each card names it
#     "description": "the Student Life Office weekly newsletter",   # HTML
#     "senders": ("studentlife@",),  # found in the From address
#     "subjects": ("weekly newsletter",),   # found in the subject
#     "letterhead": ("example law", "student life office"),  # never a title
# }
FLYERS = None
STAMP = ""                # A stamp on every flyer that is not a place:
                          # r"\bapproved\s+by\s+example\s+law\b".

# --- the campus ---------------------------------------------------------------

# One entry per building: (pattern, full name, short name). The pattern
# matches the building only -- room numbers are found separately -- and the
# short name is what goes beside a room: "Smith 204".
#
# VENUES = (
#     (r"smith\s+hall|100\s+main\s+st", "Smith Hall", "Smith"),
#     (r"example\s+law", "Example Law", "Example Law"),
# )
VENUES = ()
SCHOOL_VENUE = ""         # The full name of the VENUES entry that is only the
                          # school's own name ("Example Law" above).
AWAY = ""                 # Landmarks of another campus a reader would need
                          # transport to reach: r"north\s+campus|kline\s+hall".
AWAY_NAME = ""            # What the page calls it: "North Campus".
AWAY_TRAVEL = ""          # How far: "20-min bus".
HOME_GROUPS = ()          # Calendar groups that only post home-campus events.
SPACES = ()               # Named rooms: (r"moot\s+court", r"the\s+commons").
PROPER_SPACES = ()        # Of those, the ones that read before the building:
                          # "The Commons, Smith" rather than "Smith Lobby".
ROOM_PREFIXES = ()        # Building abbreviations flyers glue to a room
                          # number: ("smith",) reads "Smith204" as Smith 204.
MAIL_ROOM = r"\b(?:in|at|room|rm\.?)\s+(\d{3,4}[A-Z]?\b)"
                          # "in 204" in an email's text. Georgetown's adds its
                          # building names; see that profile.
MAIL_VENUE = ""           # A named place in an email's text, with its room:
                          # r"\b(Smith(?:\s+Hall)?(?:\s+\d{3,4})?)\b".
LAW_BUILDINGS = ()        # Short names whose events are the law school's
                          # whatever they say: ("Smith",).
LAW_GROUPS = ()           # Calendar groups whose events are the law school's.

# --- the events calendar --------------------------------------------------------

# A LiveWhale iCal feed, with {days} where the number of days goes. Leave
# empty if your school's calendar is not LiveWhale; the docket runs on mail.
CALENDAR = ""
CALENDAR_NAME = ""        # How the footer credits it (HTML):
                          # "Example&rsquo;s public events calendar".
CALENDAR_GROUP = ""       # Captures a group's slug from an event's URL:
                          # r"https://events\.example\.edu/([^/]+)/".
CALENDAR_GROUP_NAMES = {}  # {"law": "Example Law"}; "" hides a slug.

# --- words -----------------------------------------------------------------------

ORG_PREFIX = ""           # Trimmed from an org name too long to show:
                          # r"^example\s+(?:university\s+)?law\s+".
GENERIC_ORGS = ()         # Names that identify no organisation: ("example law",).
PROPER_NOUNS = ()         # Names the title check must not call misspelt: your
                          # buildings, orgs, neighbourhoods. Lower case.
COMMON_WORDS = ()         # Words in every title, useless for matching two:
                          # ("example", "law").
LINK_DOMAINS = ()         # Domains a submitted link may be opened on:
                          # ("example.edu",).
ROOM_EXAMPLE = "Room 101"  # The submit form's placeholder: "Smith 204".
