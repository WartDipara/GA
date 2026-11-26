"""Serialize trusted runtime Skill instructions for model inputs and traces."""


def skill_prefix(contents):
    """Materialize trusted frozen instructions without relying on provider discovery."""
    return (
        "\n\n".join(
            f'<runtime_skill name="{name}">\n{text}\n</runtime_skill>' for name, text in contents
        )
        + "\n\n"
    )
