"""The assistant's typed tools (Prompt 11 Part B, B2).

Every tool wraps an existing service; none contains academic logic. Two
implementations share one protocol and one result shape:

    DbAssistantTools        the signed-in student's real records (db_tools.py)
    SyntheticTools          the eval's fabricated students (eval/world.py)

## What the model can and cannot call

`MODEL_TOOLS` is the list a model sees. It has no apply: `apply_confirmed` and
`apply_preference` exist only on the protocol, and only the action executor
(`service.execute_action`) calls them, with a token minted when the UI showed
the card and spent by the student's tap. A model that names an apply tool gets
`unknown_tool` - the call never reaches the protocol.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.domain.plan_edits import PlanChange
from app.services.assistant.contracts import Facts, facts_from

COURSE = r"^\d{2}:\d{3}:\d{3}$"
TERM = r"^\d{4}[0179]$"


class AssistantTools(Protocol):
    def get_profile_summary(self) -> dict: ...
    def get_progress(self, *, include_gpa: bool) -> dict: ...
    def get_plan(self) -> dict: ...
    def explain_course_placement(self, course: str, term_code: str | None) -> dict: ...
    def list_course_options(self, course: str, term_code: str) -> dict: ...
    def search_courses(self, *, text: str, term_code: str | None,
                       requirements: list[list[str]] | None, topic: str | None,
                       filters: dict | None, exclude: list[str], limit: int) -> dict: ...
    def get_course_details(self, course: str) -> dict: ...
    def preview_plan_change(self, change: PlanChange) -> dict: ...
    def find_sections(self, course: str, term_code: str, limit: int) -> dict: ...
    def preview_preference(self, preference: dict) -> dict: ...
    # -- never in MODEL_TOOLS: the action executor's, after a tap ----------
    def apply_confirmed(self, preview_id: str) -> dict: ...
    def apply_preference(self, preference: dict) -> dict: ...
    def plan_revision(self) -> int | None: ...


# --------------------------------------------------------------------------
# Arguments, validated before any tool runs
# --------------------------------------------------------------------------


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_Args):
    pass


class ProgressArgs(_Args):
    include_gpa: bool = False


class PlacementArgs(_Args):
    course: str = Field(pattern=COURSE)
    term_code: str | None = Field(default=None, pattern=TERM)


class OptionsArgs(_Args):
    course: str = Field(pattern=COURSE)
    term_code: str = Field(pattern=TERM)


class SearchFiltersArgs(_Args):
    credits: Decimal | None = Field(default=None, gt=0, le=30)
    online: bool | None = None
    async_only: bool | None = None
    in_person: bool | None = None
    campus: str | None = Field(default=None, max_length=40)
    earliest: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    latest: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    avoid_days: list[Literal["M", "T", "W", "H", "F", "S", "U"]] | None = None
    level: int | None = Field(default=None, ge=1, le=4)
    open_only: bool | None = None
    fills_two: bool | None = None


class SearchArgs(_Args):
    text: str = Field(default="", max_length=300)
    term_code: str | None = Field(default=None, pattern=TERM)
    requirements: list[list[str]] | None = Field(default=None, max_length=4)
    topic: str | None = Field(default=None, max_length=120)
    #: The model's expansion of a vague topic ("something about tech" ->
    #: computer, technology, data...). Search is BM25 + aliases, not semantic:
    #: words it was never given cannot match.
    keywords: list[str] = Field(default_factory=list, max_length=12)
    filters: SearchFiltersArgs | None = None
    exclude: list[str] = Field(default_factory=list, max_length=48)
    limit: int = Field(default=10, ge=1, le=10)


class DetailsArgs(_Args):
    course: str = Field(pattern=COURSE)


class PreviewArgs(_Args):
    change: PlanChange


class SectionsArgs(_Args):
    course: str = Field(pattern=COURSE)
    term_code: str = Field(pattern=TERM)
    limit: int = Field(default=3, ge=1, le=3)


class PreferenceArgs(_Args):
    delivery: Literal["online", "async", "hybrid", "in_person", "no_preference"] | None = None
    avoid_days: list[Literal["M", "T", "W", "H", "F", "S", "U"]] | None = None
    earliest_start: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    latest_end: str | None = Field(default=None, pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    max_credits_per_term: int | None = Field(default=None, ge=6, le=24)


class PreferenceToolArgs(_Args):
    preference: PreferenceArgs


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args: type[_Args]
    activity: str


MODEL_TOOLS: dict[str, ToolSpec] = {s.name: s for s in (
    ToolSpec("get_profile_summary", "The student's program, catalog year, support label and "
             "saved planning preferences (delivery, days, times, workload).", NoArgs,
             "Checked your profile"),
    ToolSpec("get_progress", "Requirements done / in progress / open, with the source label "
             "(estimated or from the Degree Navigator audit). include_gpa only when the "
             "student asked about GPA.", ProgressArgs, "Checked your progress"),
    ToolSpec("get_plan", "The saved plan: terms, courses, credits, states (locked / chosen), "
             "graduation term.", NoArgs, "Checked your plan"),
    ToolSpec("explain_course_placement", "Why the planner put a course in its term "
             "(requirements, prerequisites, offering).", PlacementArgs,
             "Looked up why it is there"),
    ToolSpec("list_course_options", "Valid alternatives for a PLANNED course: same requirement "
             "and different requirement groups; locks and move terms.", OptionsArgs,
             "Found options"),
    ToolSpec("search_courses", "Valid courses by requirement codes (from tools, never "
             "guessed), topic keywords and filters. Requirements within ONE inner list are OR; "
             "separate inner lists are AND. For any remaining requirement, use one list of alternatives. "
             "async_only means asynchronous; online includes async. No school-code restriction. "
             "open_only is optional, not a default. REQUIRED for any 'a different X / "
             "another Y / something about Z' request.", SearchArgs, "Searched courses"),
    ToolSpec("get_course_details", "Public catalog data for one course: title, credits, "
             "description, published prerequisite text, offering.", DetailsArgs,
             "Read the catalog entry"),
    ToolSpec("preview_plan_change", "What a change would do (nothing is saved): swap / move / "
             "remove / add / lock / unlock / lighten_term / choose_alternative.", PreviewArgs,
             "Previewed the change"),
    ToolSpec("find_sections", "The best 1-3 sections of a course in a term that respect the "
             "student's blocked times.", SectionsArgs, "Checked sections"),
    ToolSpec("update_planning_preference", "Preview a change to saved planning preferences "
             "(saved only after the student confirms).", PreferenceToolArgs,
             "Previewed the preference"),
)}


@dataclass
class ToolOutcome:
    name: str
    args: dict
    ok: bool
    data: Any = None
    error: str | None = None
    facts: Facts = field(default_factory=Facts)
    activity: str = ""


def _count_label(name: str, data: dict) -> str:
    base = MODEL_TOOLS[name].activity
    if name == "search_courses":
        return f"Found {len(data.get('results') or [])} courses"
    if name == "list_course_options":
        n = sum(len(g.get("options") or []) for g in data.get("groups") or [])
        return f"Found {n} options"
    if name == "find_sections":
        return f"Found {len(data.get('sections') or [])} sections"
    return base


def run_tool(tools: AssistantTools, name: str, raw_args: Any, *,
             gpa_allowed: bool) -> ToolOutcome:
    """Validate and run one model-requested tool. Never raises: a bad call is
    an outcome the model sees (and the eval scores)."""
    spec = MODEL_TOOLS.get(name)
    args = raw_args if isinstance(raw_args, dict) else {}
    if spec is None:
        return ToolOutcome(name, args, False, error="unknown_tool")
    try:
        parsed = spec.args.model_validate(args)
    except ValidationError as exc:
        return ToolOutcome(name, args, False,
                           error="invalid_arguments: " + "; ".join(
                               f"{'.'.join(map(str, e['loc']))}: {e['msg']}"
                               for e in exc.errors()[:4]))
    try:
        data = _dispatch(tools, name, parsed, gpa_allowed=gpa_allowed)
    except LookupError as exc:
        return ToolOutcome(name, args, False, error=f"not_found: {exc}")
    except ValueError as exc:
        return ToolOutcome(name, args, False, error=f"refused: {exc}")
    return ToolOutcome(name, parsed.model_dump(mode="json", exclude_none=True), True, data,
                       facts=facts_from(name, data), activity=_count_label(name, data))


def _dispatch(tools: AssistantTools, name: str, a: Any, *, gpa_allowed: bool) -> dict:
    match name:
        case "get_profile_summary":
            return tools.get_profile_summary()
        case "get_progress":
            # Privacy: GPA reaches the model only when the STUDENT asked about
            # it - whatever the model requested.
            return tools.get_progress(include_gpa=bool(a.include_gpa and gpa_allowed))
        case "get_plan":
            return tools.get_plan()
        case "explain_course_placement":
            return tools.explain_course_placement(a.course, a.term_code)
        case "list_course_options":
            return tools.list_course_options(a.course, a.term_code)
        case "search_courses":
            topic = a.topic
            if a.keywords:
                topic = " ".join(dict.fromkeys([*(topic.split() if topic else []),
                                                *a.keywords]))[:120]
            filters = a.filters.model_dump(exclude_none=True) if a.filters else None
            return tools.search_courses(text=a.text, term_code=a.term_code,
                                        requirements=a.requirements, topic=topic,
                                        filters=filters, exclude=a.exclude, limit=a.limit)
        case "get_course_details":
            return tools.get_course_details(a.course)
        case "preview_plan_change":
            return tools.preview_plan_change(a.change)
        case "find_sections":
            return tools.find_sections(a.course, a.term_code, a.limit)
        case "update_planning_preference":
            return tools.preview_preference(a.preference.model_dump(exclude_none=True))
    raise LookupError(name)  # pragma: no cover - MODEL_TOOLS guards the name


def tool_catalog() -> list[dict]:
    """The tool list as the model sees it: name, description, argument schema."""
    out = []
    for spec in MODEL_TOOLS.values():
        schema = spec.args.model_json_schema()
        out.append({"name": spec.name, "description": spec.description,
                    "args": _compact(schema)})
    return out


def _compact(schema: dict) -> dict:
    """Argument names and types only - the full JSON schema is several times
    longer and every turn pays for it in input tokens."""
    defs = schema.get("$defs", {})

    def shape(s: dict) -> Any:
        if "$ref" in s:
            return shape(defs[s["$ref"].split("/")[-1]])
        if "anyOf" in s:
            parts = [shape(x) for x in s["anyOf"] if x.get("type") != "null"]
            return parts[0] if len(parts) == 1 else parts
        if s.get("type") == "object" and "properties" in s:
            return {k: shape(v) for k, v in s["properties"].items()}
        if s.get("type") == "array":
            return [shape(s.get("items", {}))]
        if "enum" in s:
            return "|".join(map(str, s["enum"]))
        if "const" in s:
            return str(s["const"])
        return s.get("type", "any")

    return shape(schema)


__all__ = ["MODEL_TOOLS", "AssistantTools", "ToolOutcome", "ToolSpec", "run_tool",
           "tool_catalog"]
