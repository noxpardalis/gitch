# `gitch` — structured commits FTW 📜

> The structured Git Commit Helper.

`gitch` is a Git commit extractor and checker. You can use it to extract the
list of commits from a given repository and check that all those commits conform
to your configuration. This primarily exists because I like the idea of
[conventional commits][conventional-commits] but don't like how noisy it makes
the commit messages (I feel we should keep structured information with the
already machine-readable commit trailers.)

> [!NOTE]
> This is opinionated and exists to scratch my itch. I'm happy if it helps you ❤️
> and I'm sorry if it offends 😢.

## TL;DR

### Extract commits to JSON and munge them to your hearts content

```sh
gitch extract ./path/to/repo | jq '.'
```

### Check that all your commits are conforming

Make a configuration file at `your-repo/.check-commits.yaml` (or elsewhere if
you want to pass it via the `--config` flag).

Copy the following to the configuration file:

```yaml
first-commit-is-empty: true
summary:
  first-word-is-simple-verb: true
  first-word-capitalization: "upper"
trailers:
  # I see that you like the `<type>[optional scope]: <description>`
  # of conventional commits but where we're going is more… structured.

  # The <type> of your conventional commit.
  Commit-type: # NOTE: you can name this what you feel like.
    mandatory: true
    singular: true
    values:
      - build
      - chore
      - ci
      - feat
      - docs
      - style
      - refactor
      - perf
      - test
  # The [optional scope] of your conventional commit.
  Scope: # NOTE: you can name this what you feel like.
    singular: true
    # You could specify a list of values if you want to restrict what
    # scopes might be relevant in your project.
```

Then you can go ahead and check if a given repository conforms to your
configuration.

```sh
# If the repository contains the .check-commits.yaml file
gitch check ./path/to/repo
# If you want to pass the configuration path by hand.
gitch check --config ./path/to/config ./path/to/repo
```

## Installation

### Via `nix`

```sh
# Get `gitch` in a temporary shell
nix shell github:noxpardalis/gitch#gitch
# Or install to your profile
nix profile install github:noxpardalis/gitch#gitch
```

Since `gitch` uses `spaCy` for its parts-of-speech tagging it downloads a model
on first use (only when using features that need the models). If you're using
Nix in CI with your own binary cache it might be useful to use the
`gitch-with-models` output to download it once into your cache so it doesn't
need to peg the spaCy servers on each fresh CI run (if you're not using `nix`
you can do this by hand by downloading the models using the
[spaCy CLI][spacy-cli-download], cache them in your CI, and then point to the
cache directory with the `GITCH_MODEL_DIR` environment variable).

```sh
# Get `gitch` and pre-cache the models in a temporary shell
nix shell github:noxpardalis/gitch#gitch-with-models
# Or install to your profile
nix profile install github:noxpardalis/gitch#gitch-with-models
```

## Configuration file

We'll look for a configuration file at the root of the scanned repository (you
can also pass in a configuration file path via `--config`):

- `.check-commits.yaml` (or `.check-commits.yml`)

> [!NOTE]
> We present an error if you have both the `.yaml` and `.yml` at the root of
> repository because how are we supposed to know which one you want to take
> priority?

This repository has its commits checked via `gitch` and so you can take a look at
the [configuration file](.check-commits.yaml) at the root of the repository.
If you want more details on the configuration you can also check out the
[schema](#configuration-file-schema) or take a look at the
[configuration class](python/gitch/configuration.py) in the source.

### Configuration file schema

A typed version of the schema (written in Zig for its brevity) is as follows:

```zig
const std = @import("std");
const map = std.AutoHashMap;
const string = []const u8;
fn set(comptime K: type) type {
    return map(K, void);
}

/// Schema for the configuration file.
const schema = struct {
    /// Check that the first commit should have no content, i.e. you used
    /// `git commit --allow-empty`.
    ///
    /// NOTE: this check ignores the starting commit flag and will always
    /// check the first commit of the repository.
    first_commit_is_empty: bool = false,
    /// A commit reference to start checking from. Allows, you to incrementally adopt
    /// structured commits.
    starting_from: ?string = null,
    /// Checks related to the summary message of the commit, i.e. the first
    /// line containing the short description of the commit.
    summary: struct {
        /// Check that the first word of the commit is a verb. This places the message
        /// into the following sentence template:
        ///
        /// > When applied this commit will {commit-message}
        ///
        /// It then validates that the conjugation of the first word is a simple
        /// verb (i.e. has the pos tag 'VB').
        ///
        /// NOTE this performs parts-of-speech tagging and will download a model
        /// from spaCy over the internet if the model isn't found locally.
        first_word_is_simple_verb: bool = false,
        /// Check that the first letter of the first word of the commit conforms
        /// to a certain capitalization.
        ///
        /// Skipped if the configuration is not provided or explicitly set to null.
        first_word_capitalization: ?enum {
            lower,
            upper,
        } = null,
    } = .{ false, null },
    /// Checks related to the trailers of the git commit, i.e. the `key: value`
    /// pairs that may occur at the end of a commit message. The key in this map
    /// configures checks for the corresponding key in the commit trailers.
    trailers: map(string, struct {
        /// If true this key MUST be present in all commits.
        mandatory: bool = false,
        /// If true this key may only occur once per commit.
        singular: bool = false,
        /// If the set is not empty then the value of the key in the commit must
        /// correspond to one of the values in the set.
        values: set(string),
    }) = .map(),
};
```

[conventional-commits]: https://www.conventionalcommits.org/
[spacy-cli-download]: https://spacy.io/api/cli#download
