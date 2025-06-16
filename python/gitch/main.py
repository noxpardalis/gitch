"""
The Structured Git Commit Helper.

- Check your Git commit messages to ensure structure and compliance.

- Extract information from your Git commits to then generate changelogs, reports, etc...
"""

import contextlib
import json
import logging
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Annotated

import rich
import rich.live
import rich.logging
import rich.panel
import rich.progress
import spacy
import typer
import yaml

import gitch
import gitch.gitch_core as gitch_core
import gitch.misc.xdg
from gitch import attributes, did_you_mean
from gitch.configuration import Capitalization, Schema

GITCH_MODEL_DIR_VARIABLE = "GITCH_MODEL_DIR"
rich.reconfigure(stderr=True)
PROGRESS: rich.progress.Progress = rich.progress.Progress(
    rich.progress.SpinnerColumn(),
    rich.progress.TextColumn("[progress.description]{task.description}"),
    rich.progress.BarColumn(),
    rich.progress.TimeElapsedColumn(),
)


def load_spacy_model(model_dir: Path, model_name: str) -> spacy.language.Language:
    # Lookup `meta.json` to find model name.
    with (model_dir / model_name / "meta.json").open() as spacy_metadata_file:
        spacy_metadata = json.load(spacy_metadata_file)
        version = spacy_metadata["version"]
        nlp = spacy.load(
            model_dir / model_name / f"{model_name}-{version}",
            enable=["tok2vec", "tagger", "attribute_ruler"],
        )
        return nlp


cli = typer.Typer(
    no_args_is_help=True,
    help=__doc__,
    pretty_exceptions_show_locals=False,
)


@cli.callback()
def configure_logging(
    verbose: Annotated[
        int,
        typer.Option(
            "--verbose",
            "-v",
            help="Enable verbose output. Repeat for increased verbosity (e.g. -vv).",
            count=True,
            is_flag=False,
        ),
    ] = 0,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Suppress logging output.")
    ] = False,
):
    # Set up logging
    if not quiet:
        match verbose:
            case 0:
                log_level = logging.INFO
            case _:
                log_level = logging.DEBUG
        logging.basicConfig(
            level=log_level,
            format="%(message)s",
            datefmt="[%Y-%m-%d %X]",
            handlers=[rich.logging.RichHandler()],
        )


@cli.command()
def check(
    repository_path: Annotated[
        Path, typer.Argument(help="Path to a Git repository.")
    ] = Path("."),
    configuration_path: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to the configuration file "
            "(by default searches for the file at the root of git repository).",
        ),
    ] = None,
    offline: Annotated[
        bool,
        typer.Option(
            "--offline/--online",
            help="Connect to the internet to download models",
        ),
    ] = False,
):
    """
    Check your Git commits for compliance.
    """

    task = PROGRESS.add_task(f"Loading Git repository at {repository_path}", total=1)
    # find the repository
    repository = gitch_core.Repository(repository_path)

    repository_root = repository.root
    logging.info(f"found Git repository at repo_root={repository_root}")
    PROGRESS.update(task, advance=1)

    # if not provided locate the configuration file relative to the root of the
    # repository
    if configuration_path is None:
        configuration_path_yaml = repository_root / ".check-commits.yaml"
        configuration_path_yml = repository_root / ".check-commits.yml"

        # NOTE this is just to improve the quality of error messages and is not vulnerable
        # to TOCTOU.
        match (configuration_path_yaml.exists(), configuration_path_yml.exists()):
            case (True, False):
                resolved_configuration_path = configuration_path_yaml
            case (False, True):
                resolved_configuration_path = configuration_path_yml
            case (True, True):
                raise Exception(
                    f"found both '.check-commits.yaml' and '.check-commits.yml' at '{repository_root}'\n"
                    "  | context: unsure which configuration file should take priority\n"
                    "  |    help: remove one of the two files or pass in one of them explicitly via '--config'"
                )
            case _:
                raise Exception(
                    f"could not find '.check-commits.yaml' in root of repository at '{repository_root}'\n"
                    "  | context: no configuration found unable to proceed\n"
                    "  |    help: create a '.check-commits.yaml' at the root or pass in a config file explicitly via '--config'"
                )
    else:
        resolved_configuration_path = configuration_path

    task = PROGRESS.add_task(
        f"Loading configuration at {resolved_configuration_path}", total=1
    )
    # load the configuration file
    with resolved_configuration_path.open() as configuration_file:
        if resolved_configuration_path.is_relative_to(repository_root):
            logging.info(
                f"found configuration file at {{repo_root}}/{resolved_configuration_path.relative_to(repository_root)}"
            )
        else:
            logging.info(
                f"found configuration file at {{repo_root}}/{resolved_configuration_path}"
            )

        data = yaml.safe_load(configuration_file)
    configuration = Schema.model_validate(data)
    PROGRESS.update(task, advance=1)

    commit_results = {}

    commits_with_cutoff = repository.commits(
        commit_start_cutoff=configuration.starting_from
    )

    # NOTE: this commit prefix seems to work better:
    # the sentence "When applied this commit will" leads to more
    # non infinitive verbs being tagged as infinitive verbs.
    commit_prefix = "I will"

    task = PROGRESS.add_task(
        f"Performing sequential checks [{0}/{len(commits_with_cutoff)}]",
        total=len(commits_with_cutoff),
    )
    # go through each commit and perform all checks that can occur sequentially.
    for index, commit in enumerate(commits_with_cutoff):
        commit_results[commit.id] = {
            "id": commit.id,
            "summary": commit.summary,
            "errors": [],
        }
        errors = commit_results[commit.id]["errors"]

        match configuration.summary.first_word_capitalization:
            case Capitalization.LOWER:
                if not commit.summary[0].islower():
                    errors.append("summary does not begin with a lower case letter")
            case Capitalization.UPPER:
                if not commit.summary[0].isupper():
                    errors.append("summary does not begin with an upper case letter")

        for key, trailer in configuration.trailers.items():
            if values := commit.trailers.get(key):
                # the commit has this key in its trailers.
                if trailer.singular and len(values) != 1:
                    errors.append(
                        f"expected trailers['{key}'] to be singular instead it has length {len(values)}"
                    )
                if trailer.values and not values.issubset(trailer.values):
                    errors.append(
                        f"trailers['{key}'] has non-configured values {values - trailer.values}"
                    )
            else:
                # the commit does not have this key in its trailers.
                if trailer.mandatory:
                    possible_match = did_you_mean(key, commit.trailers)
                    if possible_match:
                        errors.append(
                            f"trailers['{key}'] not found but '{key}' is mandatory (found similar field: '{possible_match}')"
                        )
                    else:
                        errors.append(
                            f"trailers['{key}'] not found but '{key}' is mandatory"
                        )
        PROGRESS.update(
            task,
            advance=1,
            description=f"Performing sequential checks [{index + 1}/{len(commits_with_cutoff)}]\n"
            f"    [green]- processing {commit.id[0:7]}",
        )

    PROGRESS.update(
        task,
        description=f"Performing sequential checks [{len(commits_with_cutoff)}/{len(commits_with_cutoff)} commits]",
        completed=len(commits_with_cutoff),
    )

    # perform the batch processing of summaries.
    task = PROGRESS.add_task(
        f"Performing batched checks [{0}/{len(commits_with_cutoff)}]",
        total=len(commits_with_cutoff),
    )
    ## process the commit summaries using natural-language processing.
    if configuration.summary.first_word_is_simple_verb:
        # find the path to the model dir from an environment variable (or the default
        # XDG cache dir path if not supplied)
        model_dir = Path(
            os.environ.get(GITCH_MODEL_DIR_VARIABLE)
            or gitch.misc.xdg.cache_dir(Path("gitch"))
        ).resolve()
        model_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        # try and load the spacy model:
        #
        # if this fails attempt to download the model (if online access is permitted)
        # and try again.
        spacy_model = "en_core_web_md"
        try:
            nlp = load_spacy_model(model_dir, spacy_model)
        except Exception:
            if offline:
                raise Exception(
                    "models for parts-of-speech tagging not available\n"
                    "  | context: run with '--offline' and so unable to download models\n"
                    f"  |    help: rerun without '--offline' or manually download the models to '{model_dir}'"
                )
            else:
                # spaCy writes to stdout in a way that disrupts the TUI from rich so we
                # redirect this to devnull.
                with (
                    Path(os.devnull).open("w") as devnull,
                    contextlib.redirect_stdout(devnull),
                ):
                    spacy.cli.download(
                        spacy_model,
                        False,
                        True,
                        "--target",
                        f"{model_dir}",
                        "--quiet",
                    )
            nlp = load_spacy_model(model_dir, spacy_model)

        summary_batch = []
        for commit in commits_with_cutoff:
            commit_message = f"{commit_prefix} {commit.summary.lower()}"
            summary_batch.append(commit_message)

        for index, commit_nlp in enumerate(nlp.pipe(summary_batch)):
            commit = commits_with_cutoff[index]
            errors = commit_results[commit.id]["errors"]

            start = commit_nlp[2]

            verb_forms = start.morph.get("VerbForm")
            verb_form = verb_forms[0] if len(verb_forms) == 1 else None

            match (start.pos.name, verb_form):
                case ("VERB", "Inf"):
                    pass
                case ("VERB", verb_form):
                    first_word = commit.summary.split(maxsplit=1)[0]
                    errors.append(
                        f"summary does not begin with a simple verb: '{first_word}' is conjugated as {verb_form}"
                    )
                case _:
                    first_word = commit.summary.split(maxsplit=1)[0]
                    errors.append(
                        f"summary does not begin with a verb: '{first_word}' is a {start.pos.name}"
                    )
            PROGRESS.update(
                task,
                advance=1,
                description=f"Performing batched checks [{index + 1}/{len(commits_with_cutoff)}]\n"
                f"    [green]- processing {commit.id[0:7]}",
            )
    PROGRESS.update(
        task,
        description=f"Performing batched checks [{len(commits_with_cutoff)}/{len(commits_with_cutoff)} commits]",
        completed=len(commits_with_cutoff),
    )

    # perform the processing of special-cases.
    task = PROGRESS.add_task("Performing special case checks", total=1)
    if configuration.first_commit_is_empty:
        first_commit = repository.first_commit()
        if repository.diff(first_commit, gitch_core.Algorithm.Myers) is not None:
            if first_commit.id not in commit_results:
                commit_results[first_commit.id] = {
                    "id": first_commit.id,
                    "summary": first_commit.summary,
                    "errors": [],
                }

            commit_results[first_commit.id]["errors"].append(
                "expected first commit to be an empty commit"
            )
    PROGRESS.update(task, advance=1)

    # pull out the commits that had errors.
    errors = [result for result in commit_results.values() if result["errors"]]

    if sys.stdout.isatty():
        if errors:
            rich.print_json(
                data=errors,
                sort_keys=True,
            )
    else:
        with rich.console.Console() as console:
            console.print_json(
                data=errors,
                sort_keys=True,
            )

    if errors:
        logging.error(
            f"checks failed: {len(errors)}/{len(commit_results)} commits had violations"
        )
        exit(1)
    else:
        logging.info(
            f"checks passed: {len(commit_results)}/{len(commit_results)} commits were valid",
        )


class DiffAlgorithm(str, Enum):
    histogram = "histogram"
    myers = "myers"
    myers_minimal = "myers-minimal"

    def to_gitch_algorithm(self) -> gitch_core.Algorithm:
        match self:
            case DiffAlgorithm.histogram:
                return gitch_core.Algorithm.Histogram
            case DiffAlgorithm.myers:
                return gitch_core.Algorithm.Myers
            case DiffAlgorithm.myers_minimal:
                return gitch_core.Algorithm.MyersMinimal


@cli.command()
def extract(
    repository_path: Annotated[Path, typer.Argument(help="Path to a git repository.")],
    with_diff: Annotated[
        DiffAlgorithm | None,
        typer.Option(
            help="Include diffs with the commit extraction.",
        ),
    ] = None,
    commit_start_cutoff: Annotated[
        str | None,
        typer.Option(
            "--start-commit", help="Earliest commit to include in the extraction"
        ),
    ] = None,
    commit_end_cutoff: Annotated[
        str | None,
        typer.Option("--end-commit", help="Latest commit to include in the extraction"),
    ] = None,
    cutoff_start_timestamp: Annotated[
        str | None,
        typer.Option(
            "--start-timestamp",
            "-s",
            help="Earliest time of commit to include (as an ISO timestamp)",
        ),
    ] = None,
    cutoff_end_timestamp: Annotated[
        str | None,
        typer.Option(
            "--end-timestamp",
            "-e",
            help="Latest time of commit to include (as an ISO timestamp)",
        ),
    ] = None,
):
    """
    Extract information from your Git commits.
    """

    # find the repository
    repository = gitch_core.Repository(repository_path)

    commits = repository.commits(
        commit_start_cutoff=commit_start_cutoff,
        commit_end_cutoff=commit_end_cutoff,
        cutoff_start_timestamp=cutoff_start_timestamp,
        cutoff_end_timestamp=cutoff_end_timestamp,
    )

    # for each commit extract the commit data
    task = PROGRESS.add_task(
        f"Extracting commits: [{0}/{len(commits)}]",
        total=len(commits),
    )
    processed_commits = []
    for index, commit in enumerate(commits):
        # convert from the Rust supplied attributes to a Python dict
        data = attributes(commit)

        # if diffs should be included (expensive!)
        if with_diff is not None:
            # perform the diff on the commit.
            data["diff"] = repository.diff(commit, with_diff.to_gitch_algorithm())

        processed_commits.append(data)
        PROGRESS.update(
            task,
            advance=1,
            description=f"Extracting commits [{index + 1}/{len(commits)}]\n"
            f"    [green]- processing {commit.id[0:7]}",
        )

    PROGRESS.update(
        task,
        description=f"Extracting commits [{len(commits)}/{len(commits)} commits]",
        completed=len(commits),
    )

    # output the extracted commit information as json
    task = PROGRESS.add_task(
        "Preparing output",
        total=1,
    )

    if sys.stdout.isatty():
        rich.print_json(
            data=processed_commits,
            sort_keys=True,
        )
    else:
        with rich.console.Console() as console:
            console.print_json(
                data=processed_commits,
                sort_keys=True,
            )

    PROGRESS.update(task, advance=1)


def main():
    with rich.live.Live(
        rich.panel.Panel(PROGRESS, title="gitch check"),
        redirect_stdout=False,
        refresh_per_second=10,
    ):
        cli()


if __name__ == "__main__":
    main()
