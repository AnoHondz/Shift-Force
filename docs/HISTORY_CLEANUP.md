# Historical exposure response

Five original commits contain `.env`, `employee_mgmt.db`, and in later commits a database backup. Removing them from the current branch does not remove their historical objects or revoke an exposed credential.

The local workspace signing key has been rotated, and obsolete bootstrap passwords/codes and unused legacy database-password settings have been removed from the local environment. The exposure inventory found no remaining local environment values matching the four historical secret values. The running synthetic demo uses its own independently generated key and generated passwords; its workforce values and account passwords were preserved. Authentication migration `0006_operations` replaces numeric session identities with revocable random tokens, invalidating pre-migration cookies. Subsequent password changes, role changes, or session revocation invalidate existing session/remember cookies and pending recovery links; unfinished MFA enrollments are discarded while enabled MFA remains protected.

## Reviewable cleanup

Use the inventory tool without printing credential or employee contents:

```text
python -m scripts.exposure_inventory --output artifacts/exposure-review.json
python -m scripts.prepare_history_cleanup --destination artifacts/NEW-SCRUBBED-MIRROR.git
```

The cleanup tool creates a separate ignored mirror, removes the sensitive paths from all reachable commits, replaces historical credential literals in source, and verifies that neither the sensitive paths nor those literals survive. It verifies that the original repository refs are unchanged. It does not push, rewrite the working repository, remove local databases, or change `master`. A verified mirror was prepared locally during this review; its private report is under `artifacts` and is intentionally not committed.

Publishing that mirror would rewrite shared branch history and needs a separately approved maintenance window, ref inventory, coordination with collaborators/forks, and a concrete forced-update plan. Review the exact old/new refs first. The current pull request and branch history remain intact until that approval. Provider caches, forks, clones, and earlier downloads can retain removed data; history cleanup is not a substitute for rotation or an incident response. Follow [GitHub's sensitive-data removal guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## Operator evidence still required

Before using an existing installation with real staff:

1. Inventory every affected account and external provider credential. Revoke or rotate each with its provider; record verification dates and ticket references without storing secret values in this repository.
2. Reset exposed account passwords using verified identity and `flask reset-password`. Use `flask revoke-all-sessions` for the explicitly configured database and repeat for every store if needed. The CLI requires typed scope confirmation. Do not assume rotating one provider or one store resolves the others.
3. Assess which employee information and conversations were exposed, who could access them, affected people, applicable response obligations, and the responsible incident contact. Retain evidence privately and obtain the organization's review. The inventory does not complete that assessment.
4. Review and approve the prepared history cleanup; arrange for old clones to be replaced or cleaned and contact the hosting provider where historical cache removal is needed.
5. Keep the exposure inventory, provider rotation receipts, password recovery evidence, and incident decision together in a private operator system. Never mark these complete based solely on a clean source scan.

External provider rotation and employee-data response have not been independently confirmed. No notification emails or other external messages were sent by this work.
