"""General parser registered via ``parsers_dirs`` (file stem = parser name)."""


def parse(raw: str, result: object) -> str:
    del result
    return raw.strip().upper()
