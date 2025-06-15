from pydantic.alias_generators import to_snake


def to_kebab(name: str) -> str:
    """Convert a PascalCase, camelCase, or snake_case string to kebab-case.

    Args:
        camel: The string to convert.

    Returns:
        The converted string in kebab-case.
    """
    snake = to_snake(name)
    kebab = snake.replace("_", "-")
    return kebab.lower()
