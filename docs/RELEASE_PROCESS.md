# Source release process

Use a clean exact source commit. Preserve public history and old tags. Software
version is VERSION; private npm components may have independent version numbers.

1. Complete scope/privacy/resource/history review, clean installation, upgrade,
   recovery and browser acceptance. Retain applicable third-party terms.
2. Push the reviewed branch and create a PR. Repair failures and require both
   real CI jobs before merging. Do not bypass the checks.
3. Check out and validate the merged commit, create a new Tag and wait for Tag
   validation of that same commit. Never move an existing public Tag.
4. Build archives and metadata into a new directory outside the clean checkout:

```sh
python scripts/package_release.py --ref v0.2.0-rc.1 --output /YOUR_TEMP/vorntek-0.2.0-rc.1
```

The command refuses dirty source, a ref differing from HEAD and reused output.
Git-tracked files provide the zip/tar.gz; release-manifest.json binds source,
website, version, SQL and dependency identity. SHA256SUMS verifies those downloads.
The packager does not upload files or need production credentials.

5. Publish the pre-release with its notes and verified source artifacts. Download
   anonymously, verify hashes and install using only public documentation. Check
   business behavior and restart persistence again.
6. Record exact PR/CI/Tag/Release and download acceptance. Important defects in a
   public version get a new version, not silently replaced old bytes.

Source-archive installation needs no Git. Git is needed for development/history
review or generating another release. Never distribute `.env`, `.secrets`,
runtime data, customer exports, backups or private operational receipts.
GitHub publication does not automatically deploy to either running website.
