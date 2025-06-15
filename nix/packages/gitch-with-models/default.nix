{
  pkgs,
  perSystem,
  ...
}:
let
  pos-models = pkgs.python3Packages.buildPythonPackage rec {
    pname = "pos-models";
    version = "3.8.0";
    format = "wheel";

    nativeBuildInputs = [
      (pkgs.python3.withPackages (
        python-pkgs: with python-pkgs; [
          pip
        ]
      ))
    ];

    src = pkgs.fetchurl {
      url = "https://github.com/explosion/spacy-models/releases/download/en_core_web_md-${version}/en_core_web_md-${version}-py3-none-any.whl";
      hash = "sha256-XmMp/j/s7bHRoCw+ohcu4P7ebOpuSu+2oC2DLbp4oxA=";
    };

    installPhase = ''
      mkdir -p $out
      pip install dist/*.whl --target $out
    '';
  };
  application = perSystem.self.gitch;
in
pkgs.stdenv.mkDerivation {
  inherit (application) name;
  inherit (application) version;
  dontUnpack = true;
  buildInputs = [ pkgs.makeWrapper ];

  installPhase = ''
    mkdir -p $out/bin
    cp ${application}/bin/* $out/bin/

    wrapProgram $out/bin/gitch \
      --prefix GITCH_MODEL_DIR : ${pos-models}
  '';
}
