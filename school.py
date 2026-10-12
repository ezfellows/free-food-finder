"""The school this docket is for.

Everything true of one school and not another lives in a profile under
`schools/`: its mail domains, how a person's address differs from an
organisation's, its buildings and rooms, its events calendar, and the
newsletter that carries its flyers. The rest of the code asks this module and
never names a school itself.

Pick one in data/config.json -- {"school": "georgetown_law"} -- or with
FFF_SCHOOL in the environment, which wins.

With none picked, the docket knows no school. The mail rules then refuse
everything except the senders and lists added with `./fff sources`, and no
event is placed on any campus. That is deliberate. The rule that tells a
classmate from a student org at Georgetown -- a netID always carries a digit --
would, at a school whose addresses read first.last@, wave every classmate's
mail through as an organisation's. A school earns its rules by having a
profile, and until it has one the safe answer is no.
"""

from __future__ import annotations

import importlib
import os
import re
import types
from contextlib import contextmanager

import config

# A profile is a module name: lowercase, no dots, no leading underscore. The
# underscore keeps schools/_template.py from ever being chosen by accident.
PROFILE_NAME = re.compile(r"[a-z][a-z0-9_]*")

# Matches nothing. A setting left empty must never match by accident.
NEVER = re.compile(r"(?!)")


class Profile:
    """What the code may assume about a school.

    The defaults describe a school nobody has told it about. Every setting is
    documented here; `schools/_template.py` is the same list, ready to copy.
    """

    # --- the school --------------------------------------------------------
    NAME = ""                 # How the page names it: "Georgetown Law".
    CAMPUS = ""               # The campus this docket covers: "Capitol Campus".
    TIMEZONE = ""             # For the calendar feed; "" leaves times floating.

    # --- mail: who counts as an organisation ---------------------------------
    DOMAINS = ()              # The school's own mail domains; subdomains count.
    PERSON = ""               # Found in the local part of every person's
                              # address. "" means every address is a person's.
    BROADCAST_DOMAINS = ()    # Mail-merge platforms that send nothing personal.
    LISTSERVS = ""            # An address that reaches every student.
    LAW_FIRM_MAIL = False     # A law firm's recruiting mail to the whole class.
    TRACKING_HOSTS = ()       # Hosts whose links carry a token naming the reader.
    CAMPUSGROUPS_FOOTER = ""  # The line under an org's name in its footer.

    # --- the flyer newsletter ---------------------------------------------
    # None, or {"label", "senders", "subjects", "letterhead", "description"}.
    FLYERS = None
    STAMP = ""                # An approval stamp printed on every flyer.

    # --- the campus -------------------------------------------------------
    VENUES = ()               # (pattern, full name, short name) per building.
    SCHOOL_VENUE = ""         # The VENUES full name that is only the school's.
    AWAY = ""                 # Landmarks on the university's other campus.
    AWAY_NAME = ""            # What that campus is called: "Main Campus".
    AWAY_TRAVEL = ""          # How far it is: "25-min shuttle".
    HOME_GROUPS = ()          # Calendar groups that publish only home events.
    SPACES = ()               # Named spaces inside a building: r"the\s+whisk".
    PROPER_SPACES = ()        # Those that read before the building.
    ROOM_PREFIXES = ()        # A building glued to its room: "McD520".
    MAIL_ROOM = r"\b(?:in|at|room|rm\.?)\s+(\d{3,4}[A-Z]?\b)"
    MAIL_VENUE = ""           # A named place in an email's own words.
    LAW_BUILDINGS = ()        # Short names whose events are the law school's.
    LAW_GROUPS = ()           # Calendar groups whose events are the law school's.

    # --- the events calendar -----------------------------------------------
    CALENDAR = ""             # A LiveWhale iCal URL, with {days}.
    CALENDAR_NAME = ""        # How the page credits it.
    CALENDAR_GROUP = ""       # Captures a group's slug from an event's URL.
    CALENDAR_GROUP_NAMES = {}  # slug: readable name; "" hides it.

    # --- words -------------------------------------------------------------
    ORG_PREFIX = ""           # Trimmed from an org name too long to show.
    GENERIC_ORGS = ()         # Names that identify no organisation.
    PROPER_NOUNS = ()         # Words the title check must not call misspelt.
    COMMON_WORDS = ()         # Too common to say two titles are one event.
    LINK_DOMAINS = ()         # Domains a submitted link may be opened on.
    ROOM_EXAMPLE = "Room 101"  # The submit form's placeholder.

    def __init__(self, module=None):
        self.id = (module.__name__.rpartition(".")[2]
                   if module is not None else "")
        if module is not None:
            for key, value in vars(module).items():
                if key.startswith("_") or not key.isupper():
                    continue
                if not hasattr(Profile, key):
                    # A misspelt setting would silently keep its default,
                    # and a default here means "refuse".
                    raise ValueError(f"schools/{self.id}.py: unknown setting {key}")
                _check(self.id, key, value)
                setattr(self, key, value)
        self._derived = {}
        self.person = re.compile(self.PERSON)
        self.listservs = re.compile(self.LISTSERVS, re.I) if self.LISTSERVS else NEVER
        self.away = re.compile(self.AWAY, re.I) if self.AWAY else NEVER
        self.stamp = re.compile(self.STAMP, re.I) if self.STAMP else NEVER
        self.venues = [(re.compile(p, re.I), full, short)
                       for p, full, short in self.VENUES]

    @property
    def configured(self) -> bool:
        return bool(self.id)

    def derive(self, key: str, build):
        """`build(self)`, worked out once per profile and kept."""
        try:
            return self._derived[key]
        except KeyError:
            value = self._derived[key] = build(self)
            return value


def _check(profile: str, key: str, value) -> None:
    """Refuse a setting of the wrong shape before it is used.

    `DOMAINS = "example.edu"` is a string, and iterating a string gives its
    letters: every address containing an "e" would count as the school's.
    """
    default = getattr(Profile, key)
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, str):
        ok = isinstance(value, str)
    elif isinstance(default, dict):
        ok = isinstance(value, dict)
    elif isinstance(default, tuple):
        ok = isinstance(value, (tuple, list, set, frozenset))
    else:                                   # FLYERS: None or a dict
        ok = value is None or isinstance(value, dict)
    if not ok:
        raise ValueError(f"schools/{profile}.py: {key} should be "
                         f"{type(default).__name__ if default is not None else 'a dict'}, "
                         f"not {type(value).__name__}")


_active = None


def chosen() -> str:
    """The profile asked for: FFF_SCHOOL, else data/config.json, else none."""
    if "FFF_SCHOOL" in os.environ:
        return os.environ["FFF_SCHOOL"].strip()
    return str(config.load().get("school") or "").strip()


def load(name) -> Profile:
    """A profile by name, a module, or None for a school nobody described."""
    if isinstance(name, Profile):
        return name
    if isinstance(name, types.ModuleType):
        return Profile(name)
    if not name:
        return Profile()
    if not PROFILE_NAME.fullmatch(name):
        raise ValueError(f"not a profile name: {name!r}")
    try:
        module = importlib.import_module(f"schools.{name}")
    except ModuleNotFoundError as exc:
        if exc.name != f"schools.{name}":
            raise
        raise ValueError(f"no school profile schools/{name}.py") from None
    return Profile(module)


def active() -> Profile:
    global _active
    if _active is None:
        _active = load(chosen())
    return _active


def use(name) -> Profile:
    """Make a profile the active one, for the rest of the run."""
    global _active
    _active = load(name)
    return _active


@contextmanager
def using(name):
    """A profile for the length of a `with` block, then back."""
    global _active
    before = _active
    _active = load(name)
    try:
        yield _active
    finally:
        _active = before
