"""The encoding pipeline for a new major (Prompt 08 B1, docs/ENCODING_MAJORS.md).

    source(s) -> archive (sources.py) -> encoding (JSON, by hand)
              -> validate (validate.py) -> review packet (packets.py)

Every step is repeatable; none of them decides that an encoding is correct.
"""
