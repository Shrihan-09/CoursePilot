# Tool skills

Descriptions of the tools the agent may call. Each tool description is the
contract the model reasons about, so it must state precisely what the tool
does and does not guarantee.

Planned tools (NOT IMPLEMENTED YET):

- `search_courses`      - hybrid retrieval over the course catalog
- `get_course`          - exact lookup by course code
- `get_student_record`  - completed courses and standing
- `get_requirements`    - authoritative requirement tree for a program
- `get_offerings`       - sections offered in a given term
- `validate_plan`       - run the deterministic validator

Note that `validate_plan` is a tool the agent calls but cannot override. The
validator returns a verdict; the agent may replan in response, but it may not
report a plan as valid when the verdict says otherwise.
