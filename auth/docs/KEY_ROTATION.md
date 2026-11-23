# Rotating the data keys of auth

Two key families protect personal data: **field encryption** (phone, email, TOTP secret, signing-key private half; AES-256-GCM) and **lookup hashing** (HMAC for finding a user by phone or email). Both are versioned (`regisse_kit.keyring`). The pre-versioning key (`AUTH_FIELD_ENCRYPTION_KEY`, `AUTH_HASH_KEY`) is key id `0`.

Rotate on a schedule (yearly), and at once if a key may have leaked. Rehearse on staging with a restore of the production dump first. Take a backup right before step 3.

1. **Generate** new keys: `python -m app.cli gen-keys` (use the two values as new keys).
2. **Add and make current** (environment of web and scheduler), then redeploy:
   `AUTH_FIELD_KEYS_JSON={"1":"<new field key>"}` `AUTH_FIELD_KEY_CURRENT=1` `AUTH_HASH_KEYS_JSON={"1":"<new hash key>"}` `AUTH_HASH_KEY_CURRENT=1`.
   From now on new data uses key 1; old data is still read (envelope key id, or the legacy key `0` for unversioned blobs); lookups try every key; pending one-time codes still verify.
3. **Rotate the data** (online, batches, safe to re-run): `python -m app.cli rotate-data-keys` (dry run: counts of rows left), then `... --apply`.
4. **Verify**: `python -m app.cli rotate-data-keys --verify` must exit 0 (nothing left under another key).
5. **Retire the old key**: replace the values of `AUTH_FIELD_ENCRYPTION_KEY` and `AUTH_HASH_KEY` (key id `0`) with freshly generated unused keys (a tombstone: nothing is encrypted under it) and redeploy. Keep a sealed offline copy of the old keys for the length of your backup retention: **older database backups are still encrypted under them**.
6. Update the key vault entry and the record of who can read it.

Notes: the signing key (JWT) rotates separately with `rotate-key`. Tokens, refresh-token hashes and password hashes are not affected. If a key leaked, step 3 is urgent and step 5 makes the leaked key useless for the live database, not for old backups: re-encrypt or destroy those too.
