# Provider retry checkpoint

Apply the two source files as an overlay to the fixed ad5f7c40 stage-projection snapshot. provider.patch contains the sole production-function change. The prepared/run guard remains; first-seen exact-clock admission, principal/payload binding and idempotent replay belong to the authoritative RPC. No provider duplicate ledger was introduced.

The author and coordinator each ran the three exact cases with actual package calls and in-memory transport. The coordinator additionally executed parent-regression.txt unchanged against the real helpers. This is not service or native-flight acceptance.

Run from the reconstructed aero-bench package: `python -m pytest tests/providers/test_native_parcel_provider_retry.py -q`. The RPC/runtime and their fixture modules match the fixed base byte for byte (manifest). Original decision time remains tick3 while the current state stays in_transit after tick4; retries neither rollback nor add transfers. Both negative cases assert the concrete authority exception and message.

The positive pytest case shares setup through a helper; it preserves the supplied assertions, rather than being a literal text copy. parent-regression.txt preserves the exact supplied source and its separate pass is recorded. Author before.log also contains fixture-bringup assertion mistakes; the original coordinator reproduction establishes the provider gate independently. The author's broader neighbor run reported63 passed/one setup error in the retained wide-turn service fixture; that error is outside this checkpoint and is not treated as a passed service test.

GLM ordinary Flash authored both source changes in the existing a400 session. The coordinator reviewed, independently replayed and published evidence. route-receipt.json includes only effective route fields from the original assigned session. The other stage/verifier author remains separate.
