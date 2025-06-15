{
  pkgs,
  inputs,
  ...
}:
let
  src = ../../../.;
  name = "gitch";

  # TODO(noxpardalis): the Rust dependencies cache fine but the PyO3 dep
  # seems to constantly rebuild when only the Python main has changed.
  #
  # NOTE this seems to happen even when doing non-nix `uv build`s and
  # `cargo build`s.
  #
  # crane: https://github.com/ipetkov/crane/issues/414
  # pyo3: https://github.com/PyO3/pyo3/discussions/3173
  rust = rec {
    toolchain = pkgs.rust-bin.fromRustupToolchainFile "${src}/rust-toolchain.toml";
    platform = pkgs.makeRustPlatform {
      inherit (toolchain) cargo;
      inherit (toolchain) rustc;
    };
    crane-lib = (inputs.crane.mkLib pkgs).overrideToolchain toolchain;
    common-args = {
      src = crane-lib.cleanCargoSource src;
      strictDeps = true;
      doNotLinkInheritedArtifacts = true;
    };
    dependencies = crane-lib.buildDepsOnly (
      pkgs.lib.recursiveUpdate common-args {
        nativeBuildInputs = [
          python.interpreter
        ];
      }
    );
  };

  python = {
    interpreter = pkgs.python3;
  };
in
rust.dependencies
