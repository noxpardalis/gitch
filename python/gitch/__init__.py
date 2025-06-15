from pathlib import Path
from typing import Any, Iterable


def find_repository_root(repository: Path) -> Path | None:
    repository = repository.resolve(strict=True)
    root_repository = None

    while repository != repository.anchor:
        if (repository / ".git").exists():
            root_repository = repository
            break
        repository = repository.parent

    return root_repository


def levenshtein_distance(str1: str, str2: str) -> int:
    """
    Compute the levenshtein distance between two strings.
    """
    str1 = str1.lower()
    str2 = str2.lower()

    if not str1:
        return len(str2)
    if not str2:
        return len(str1)

    dcol = list(range(0, len(str2) + 1))
    t_last = 0

    for i, c1 in enumerate(str1):
        current = i
        dcol[0] = current + 1

        for j, c2 in enumerate(str2):
            next = dcol[j + 1]

            if c1 == c2:
                dcol[j + 1] = current
            else:
                dcol[j + 1] = min(current, next)
                dcol[j + 1] = min(dcol[j + 1], dcol[j]) + 1
            current = next
            t_last = j

    return dcol[t_last + 1]


def did_you_mean(choice: str, word_list: Iterable[str], threshold=3) -> str | None:
    """
    Determine the best matching word from the provided word list that has the
    lowest levenshtein distance.
    """
    pair = min(
        filter(
            lambda pair: pair[0] <= threshold,
            # NOTE this drops the keyword length to match the length of the choice to
            # improve the odds of catching suffix variations.
            map(
                lambda e: (levenshtein_distance(choice, e[: len(choice)]), e),
                word_list,
            ),
        ),
        key=lambda x: x[0],
        default=None,
    )

    return pair and pair[1]


def attributes(object: Any) -> Any:
    def extract(o: Any) -> Any:
        if isinstance(o, (str, int, float, bool, type(None))):
            return o
        elif isinstance(o, dict):
            return {k: extract(v) for k, v in o.items()}
        elif isinstance(o, (list, tuple, set)):
            return [extract(item) for item in o]
        else:
            x = {
                name: extract(getattr(o, name))
                for name in dir(o)
                if not name.startswith("_") and not callable(getattr(o, name, None))
            }
            return x

    return extract(object)
