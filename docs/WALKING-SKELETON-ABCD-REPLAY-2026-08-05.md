# V4 walking-skeleton A/B/C/D replay — 2026-08-05

The first composed operation now runs through the migrated boundaries in one path:

`methodology preflight -> SSC context hash -> seat resolution -> local trace/OTel receipt ->
managed run receipt and closeout`.

A is the direct SSC fixture. B is the V4 MVC-style controller using SSC-backed adapters. C
refuses an unknown seat and confirms no corpus copy appears in the managed run. D is evaluated
by SSC against the structured receipt contract. This is a deterministic composition proof; it
does not make a live provider request or claim that generated prose is an oracle.

