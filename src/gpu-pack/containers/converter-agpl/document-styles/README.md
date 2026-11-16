# Document style extraction

This optional command reads original PDF, HTML and ePub documents and writes
the same lexicon JSON used by the KB builder. It is part of the separate AGPL
converter. Its PDF path imports PyMuPDF only in this process.

The KB-side document_styles.py is a launcher. Set DEEP32_CONVERTER_PYTHON to a
local converter interpreter, or DEEP32_STYLES_COMMAND to a JSON argument array
for a converter-side command. Arguments are passed without a shell. For the
container form, the command must expose both the originals and output paths
to the converter. No source documents or model weights are included here.
