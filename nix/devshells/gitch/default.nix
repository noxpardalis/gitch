{ pkgs, ... }:
pkgs.mkShell {
  packages = with pkgs; [
    uv
    cargo-deny
  ];

  # TODO(noxpardalis): get this from the per system?
  PYO3_PYTHON = "${pkgs.python3}/bin/python";

  LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
    pkgs.zlib
    pkgs.stdenv.cc.cc.lib
  ];
}
