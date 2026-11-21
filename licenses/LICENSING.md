# Licenses

Copyright (C) 2026 Jeffrey A. Cummings

Deep32 project code is licensed under the Apache License, Version 2.0
(SPDX: Apache-2.0). The full license text is in LICENSE and
licenses/Apache-2.0.txt. NOTICE at the repository root holds the attribution
notices; keep it with every copy and every derivative work, as section 4 of the
license requires, and mark the files you change.

The parts of the code that use MuPDF are licensed under the GNU Affero General
Public License, version 3 or (at your option) any later version
(SPDX: AGPL-3.0-or-later), because MuPDF is licensed under the AGPL. The full
license text is in licenses/AGPL-3.0-or-later.txt. These parts are:

- the PDF converter under src/gpu-pack/containers/converter-agpl, which imports
  PyMuPDF; its source and dependency notices remain in that folder;
- the Deep32 Library's picture and PDF renderer e26-render, whose source is in
  src/workstation/library/src/render; it links libmupdf.

Deep32 documentation is licensed under Creative Commons Attribution 4.0
International (SPDX: CC-BY-4.0). The full legal code is in LICENSE-DOCS and
licenses/CC-BY-4.0.txt.

Third-party components and content keep their own licenses and notices.
Models keep their own licenses. Deep32's own art and sounds are licensed under
CC BY-NC 4.0. HP and Microsoft artwork is used with permission and is not covered
by this project's licenses. CDE artwork is CC BY-SA 3.0 (The Open Group). The
asset ledger records the terms of every file; the code and documentation licenses
do not replace them.

External libraries, package archives, models, fonts, pictures and sounds are not
included in this source release. notices/THIRD-PARTY-NOTICES.md identifies included
source components and referenced dependencies separately. It also records the
artwork notices for the separately built images, which contain different files.

The model references in docs/release/RELEASE.json do not grant commercial model rights.
The two Jina models are under CC BY-NC 4.0; commercial use needs the publisher's
own license. The dependency and model publishers retain their own rights.
