# D32-TR-1: Deep32 design

The design reference for Deep32 0.1.0-alpha.2, written in the RFC format: the corpus and index format, the retrieval
pipeline, the agent tools, the three parts of a deployment and the protocols between them, the signed release format,
and the evaluation behind the published numbers.

| Form | File |
|---|---|
| HTML | [d32-tr-1.html](https://hellobpdu.github.io/deep32/tr/d32-tr-1.html) |
| PDF | [d32-tr-1.pdf](d32-tr-1.pdf) |
| Plain text | [d32-tr-1.txt](d32-tr-1.txt) |
| Source (xml2rfc v3) | [d32-tr-1.xml](d32-tr-1.xml) |

This version of the report is archived on Zenodo as [10.5281/zenodo.24326998](https://doi.org/10.5281/zenodo.24326998).

The reasons behind each design choice, with the alternatives and measurements, are in the
[Decision Record](../DECISIONS.md).

## Building

The text and HTML forms are generated from the XML source with xml2rfc 3.35.0:

```sh
pip install xml2rfc==3.35.0
xml2rfc --text --html d32-tr-1.xml
```

The PDF is the HTML form printed by a browser.

Copyright (C) 2026 Jeffrey A. Cummings. Licensed under CC BY 4.0.

---

<p align="center"><sub><a href="../../README.md">Overview</a> · <a href="../../INSTALL.md">Installation</a> · <a href="../HARDWARE.md">Hardware</a> · <a href="../OPTIONAL-FEATURES.md">Optional features</a> · <b>Design</b> · <a href="../DECISIONS.md">Decision record</a> · <a href="../contracts/README.md">Contracts</a> · <a href="../../adapters/README.md">Agent adapters</a> · <a href="../../SECURITY.md">Security</a> · <a href="../../CHANGELOG.md">Release notes</a></sub></p>
