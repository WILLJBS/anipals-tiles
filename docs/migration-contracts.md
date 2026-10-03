# Registered graph migration contracts

Cloud migration selects a reviewed contract from `tools/migration-contracts.json`.
This is a CI tooling registry; runtime graph catalogs and deployment inputs are
unchanged. The workflow exposes a `contract` choice; its default remains `original61`.
For `gap3`, use `release_tag=tiles-global-gaps-20261003-1` and
`pilot_region=central-america`; the twenty-tile pilot must succeed in that run
before its three full regional jobs can start. Registered groups are `original61`
(61 graphs), `gap3` (3 graphs), and `additions129` (129 graphs). Each locks exact
roster, coverage and native image file bytes by SHA256. The groups have distinct
graph identities; their tile filenames are never overlaid.

`migration_ci.prepare` loads the registration and runs the existing complete
`validate_supply` and `build_plans` gates. Every asset page is read, every part
retains its positive size/SHA/contiguous-sequence requirements, and each part URL
must belong to the exact source repository and release. READY must match the
whole registered roster, coverage, pinned image, structural reports and existing
source/native evidence requirements. A draft with 127 of 129 successful graphs
cannot migrate. Missing READY and a partial published release also fail.

Cloud calls to `migrate_release_r2.py` pass both `--contract CONTRACT` and
`--source-sha REVIEWED_CHECKOUT_SHA`. The latter must equal the actual checkout.
Individual `--roster`, `--coverage` or `--image` overrides cannot be combined with
a registered contract. The pilot receipt contract hashes the reviewed checkout,
registered definition, coverage, pinned image, exact release and part-derived
regional identities. A full migration cannot borrow a twenty-tile pilot from a
different checkout or contract. Each published cloud receipt also records its
registration and reviewed checkout explicitly. Legacy standalone invocations
without `--contract` retain their prior default paths and complete supply gates.

For schema 2 releases, the full first-pass tile inventory fingerprint must equal
the corresponding READY regional graph fingerprint before any tile upload. The
legacy three-byte READY exception remains restricted to the existing explicit
legacy tag; its authenticated parts and structural checks are still required.

The checked-in public fixture `tests/fixtures/gap-release-20261003.json` retains
actual READY bytes and asset digests from the complete published release
`tiles-global-gaps-20261003-1`. Its three independent graphs, 122 source-row native
proofs, exact coverage and image pass the same supply validator. It is evidence
of release supply validation; it is not an R2 upload receipt, production
activation, or acceptance of every actual user destination.

Run `python3 tools/migration_contract.py --check` to validate every registry
entry. Regression tests also reject changed file bytes, substituted image or
coverage, wrong part SHA/URL, an incomplete draft, a cross-checkout pilot, and a
READY graph fingerprint mismatch before any upload.
