The first summary exporter inherited `synthetic: true` from the generic
simulator provenance helper. That wrapper field is erroneous for this hardware
archive. Nested per-run measurement configs correctly identify physical H2D
measurements with `synthetic: false`. No samples or timings changed.

The corrected exporter and complete re-export are available in
`../research-h2d-verified-20261118/`. This pilot is preserved, including the
exporter metadata error, rather than rewriting an earlier archive.
