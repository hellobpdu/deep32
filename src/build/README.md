# Build sources

`s5-build-driver/` contains the OpenBSD image build stages and canonical transform slots. `disc3-pins/` contains the Forge input resolver and lock helper. These are source tools; copying this folder does not run a build.

The image build requires separately supplied operating-system sets, packages, source archives, model pins and permitted media. The driver verifies the supplied input hashes. Configure the build root and input paths on an isolated build host before running any stage.

The transform contract requires the first docstring line to name `release specification section N`; strict slot numbering, anchor counts, payload hashes and reported-change checks are retained. Installer payloads beside the canonical slots are readable source files and retain their stable file-format and point-ID strings.

The pin resolver uses authenticated upstream metadata and refuses unresolved or changed pins. It requires the GPU pack kit and a release operator input record. The source tree includes no model weights, operating-system packages or signing keys.

## Source-only image inputs

The laptop-look slots retain every ordered operation from the complete lead manifests, with full before hashes, payload hashes and byte counts. Hash field names are explicitly labelled; the enclosing manifest digests cover those exact labelled bytes. Only JavaScript and launcher-wrapper source payloads are included. All image resources remain separate inputs. Normal slot execution preflights every operation and refuses before writing when any required resource or target is absent. No partial slot success is allowed. The private source projection selected code operations separately; it is not the complete image build.

The name slots contain the lead's programme-name code. Slots 1012, 1013, 1015 and 1016 additionally require their separately supplied complete, hash-bound producing manifests, imported text tables or permitted bitmap stores; those data are outside this code release. Missing manifests are a hard refusal before any write. The image build must supply its matching complete input profile.

`name-bitmaps/` contains generator source only. Its outputs incorporate separately permitted source artwork and are image-only inputs. No artwork, font or generated output is included. The generators also require the matching `title26` and `ttf` helper modules and their original permitted image/font inputs, supplied separately. Copying or parsing this code does not create any output.
