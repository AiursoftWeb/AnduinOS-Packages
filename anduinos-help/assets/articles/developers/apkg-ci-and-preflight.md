# CI Deployment and the Preflight API

Preflight avoids rebuilding a package when the authenticated destination already has every requested target version. It is a build optimization, not a replacement for upload validation or proof that APT has received the latest signed snapshot.

See [publication](Publish-Packages.md) for a small CI job and [contributing to Apkg](Contributing-to-Apkg.md) for development of the platform itself.

## Resolve versions before building

```bash
apkg guess version --path ./hello-apkg
apkg guess-version --path ./hello-apkg --json
```

The two command spellings are equivalent. Static suite and short-suite variables resolve locally. For `$(UpstreamVersion)`, resolution reads upstream index metadata using the builder's candidate-selection rules without downloading the Debian payload or running prebuild commands. Configure the upstream trust key as described in [upstream packages](Upstream-Packages.md).

Unresolved variables are errors. Preflight must not ask a server about an uncertain version. Update the version whenever fixed-version source or AppStream presentation content changes.

## Deploy behavior

```bash
apkg deploy --path ./hello-apkg \
  --source "$APKG_SOURCE" \
  --api-key "$APKG_API_KEY" \
  --skip-existing
```

Without `--skip-existing`, deploy composes publish and push. With it, deploy first resolves the complete target plan and queries the destination. It skips the build only for `allPresent: true` across the whole requested matrix. One missing target causes the normal full selected-matrix build, not a partial unverified release.

The final upload still validates hashes, ownership, duplicate slots, permissions, and downgrades. `--skip-existing` also enables duplicate skipping at upload time, covering a race where another job publishes between the preflight check and your upload.

| Preflight result | Client behavior |
|------------------|-----------------|
| All targets present | Skip build/upload work |
| Any target missing | Build and upload normally |
| HTTP 401 or 403 | Fail immediately |
| HTTP 404, 405, or server 5xx | Warn and fall back to the older full build/push workflow |
| Network failure | Warn and fall back to full build/push |
| Successful response with `Forbidden` | Fail before building |
| `NoRepository` | Continue through the legacy upload path; inspect routing warnings |

Fallback enables clients to be upgraded before servers. It does not mean an authorization failure should be ignored.

## API contract

`POST /api/packages/preflight` uses the upload API's bearer-key authentication. Requests are limited to 256 targets.

```json title="Request body"
{
  "name": "hello-apkg",
  "distro": "anduinos",
  "component": "main",
  "targets": [
    {
      "suite": "resolute-addon",
      "architecture": "all",
      "version": "1.0.0-1"
    }
  ]
}
```

```json title="Example response"
{
  "allPresent": false,
  "targets": [
    {
      "suite": "resolute-addon",
      "architecture": "all",
      "version": "1.0.0-1",
      "status": "Missing",
      "message": "At least one matching repository is missing this version.",
      "repositories": [
        {
          "id": 7,
          "name": "My AnduinOS packages (anduinos resolute-addon amd64,arm64)",
          "present": false
        }
      ]
    }
  ]
}
```

Target statuses are `Present`, `Missing`, `NoRepository`, and `Forbidden`. A target is present when an enabled local record with the exact repository, package name, version, and architecture exists in every matching repository the caller may upload to. Component filters the matching repositories but does not belong to the local duplicate slot. Family ownership is checked independently.

## Preserve development and production separation

A shared GitLab publication template can select credentials per branch while keeping every package job:

```yaml
.publish:
  stage: publish
  script:
    - cd "$PACKAGE_DIR"
    - |
      if [ "$CI_COMMIT_BRANCH" = "prod" ]; then
        APKG_SOURCE="$APKG_PROD_SOURCE"
        APKG_KEY="$APKG_PROD_API_KEY"
      else
        APKG_SOURCE="$APKG_DEV_SOURCE"
        APKG_KEY="$APKG_DEV_API_KEY"
      fi
      apkg deploy --source "$APKG_SOURCE" --api-key "$APKG_KEY" --skip-existing
```

Configure those variables and protect production credentials and the production branch in CI settings. This is an optional branch convention, not a requirement to rename your release branch. Child jobs provide `PACKAGE_DIR` and a builder with the necessary tools.

Do not remove all unchanged-directory jobs solely because their Git paths did not change: upstream-derived recipes can resolve a newer upstream package without a local source edit. The cheap version check is useful for that case. Development and production query their own servers, preserving intentional version differences.

The authenticated existence check concerns stored local records, not edge caches or client candidates. Keep the [APT publication checks](Publish-Packages.md#wait-for-repository-publication) in your release process.
