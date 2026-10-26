"""docx-to-idml's IDML writer, used by Ectype's facsimile export.

Copied unchanged from docx-to-idml (https://github.com/RexBHavgreau/docx-to-idml,
docx2idml/idml/ at commit dff3874, version 0.8.0; MIT licence, LICENSE-docx-to-idml):
`writer.py`, `model.py`, `images.py` and `base/` (InDesign's own preferences, colours, fonts
and root styles, and the elements the writer clones). Its output is checked against Adobe's
schema and has been opened in InDesign and Affinity Publisher. Ectype adds what facsimile needs
(frames placed freely on the page, the scan on a layer of its own) by subclassing, in
`ectype.idml`, so these files can be refreshed from docx-to-idml as they are.
"""
