{
  flake,
  inputs,
  pkgs,
  ...
}:
let
  treefmtEval = inputs.treefmt-nix.lib.evalModule pkgs {
    projectRootFile = "flake.nix";

    programs = {
      # Nix
      # begin-sorted start
      alejandra.enable = true;
      deadnix.enable = true;
      nixf-diagnose.enable = true;
      nixfmt.enable = true;
      statix.enable = true;
      # begin-sorted end

      # Markdown
      mdformat.enable = true;

      # YAML
      yamlfmt.enable = true;

      # TOML
      taplo.enable = true;

      # Python
      # begin-sorted start
      ruff-check.enable = true;
      ruff-format.enable = true;
      isort.enable = true;
      # begin-sorted end

      # Rust
      rustfmt.enable = true;

      # Spell-checking source code
      typos.enable = true;

      # Source-agnostic lexicographic sorting
      keep-sorted.enable = true;

      # GitHub actions.
      # begin-sorted start
      actionlint.enable = true;
      pinact.enable = true;
      # begin-sorted end
    };
  };
  formatter = treefmtEval.config.build.wrapper;
in
formatter
// {
  passthru = formatter.passthru // {
    tests = {
      check = treefmtEval.config.build.check flake;
    };
  };
}
