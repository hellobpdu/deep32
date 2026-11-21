# Workstation build inputs

This is the Workstation programme and configuration source for Deep32 1.0.0.
The folders follow the image build layout. The image build driver is in
`../build/s5-build-driver`.

Building needs OpenBSD packages and headers, the pinned Python source and wheel
inputs, the Arm optimised math sources, and the release data and media inputs.
Those inputs are obtained and verified separately by the build recipes. The
application source can be inspected independently of the images.

Upstream code keeps its own terms. The CDE catalogue source has its accompanying
`COPYING` file. The Arm routines have `LICENSE.optimized-routines`. The project
code is Apache-2.0. The exception is the picture and PDF helper e26-render in
`library/src/render/`, which links MuPDF and is AGPL-3.0-or-later. The license
texts and the complete dependency notices are at the repository root.

The launcher wrapper source is stored at `lanes/u12-ask/launcher/source/encyclopedia-wrapper.sh`. Its canonical image-tree target remains `lanes/u12-ask/launcher/out/files/d32/a/bin/e26`; its installed programme name is unchanged. This source includes the lead's slot 1013 name edits after the strict slot 1004 payload. The build slot keeps its canonical target and checks; its readable pre-name payload is `src/build/s5-build-driver/slots/laptop-look/round5-wrapper.sh`. The source filename alias makes no change to launcher behaviour. Supply the matching complete image source profile and manifests before replaying those slots.
