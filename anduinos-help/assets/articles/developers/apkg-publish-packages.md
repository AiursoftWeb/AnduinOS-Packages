# Publish Packages and Configure APT

This chapter connects [the sample package](Build-Your-First-Package.md) to [your Apkg server](Host-an-Apkg-Server.md). Use a compatible test client to check the result through ordinary APT.

Replace `https://apkg.example.com` with your server's HTTPS origin. The examples use `hello-apkg` version `1.0.0-1`; substitute your actual version after the upgrade exercise.

## Check the destination and create an API key

Confirm that your server repository accepts `anduinos`, `resolute-addon`, component `main`, with client architectures `amd64,arm64`. The sample package is architecture-independent (`all`). For a compiled package, its target architecture must be included in the repository configuration.

Sign in as the package publisher and open **API Keys**. Create a named key with a suitable expiration, and retain the displayed secret. Have the administrator grant the account the required repository upload permission. Existing package ownership is also enforced.

In a Bash terminal, read the key without placing its literal value in shell history:

```bash title="Set the upload destination"
export APKG_SOURCE='https://apkg.example.com'
read -rsp 'Apkg API key: ' APKG_API_KEY
printf '\n'
export APKG_API_KEY
```

Do not commit the key into a recipe, script, or CI YAML. The CLI receives it as an argument, so run publishing on a trusted workstation or runner and avoid shell tracing that prints expanded commands.

## Build the release bundle

```bash title="Run in the hello-apkg project"
cd ~/Source/hello-apkg
apkg lint
apkg publish
tar -tzf ./bin/hello-apkg.1.0.0-1.apkg
```

`publish` builds the selected Debian packages and writes an archive with `manifest.xml` and the package payloads. Use the archive path printed by the command. It has not contacted your server yet.

Prefer a normal `publish` for releases. `publish --no-build` bundles existing output and can pick up stale files if you have mixed versions in `bin/`.

```bash title="Upload the archive"
apkg push --file ./bin/hello-apkg.1.0.0-1.apkg \
  --source "$APKG_SOURCE" \
  --api-key "$APKG_API_KEY"
```

Read the complete result, including warnings and target routing. An upload with no matching repository does not become usable merely because an HTTP request succeeded.

## Wait for repository publication

The server first stores the uploaded packages. A repository synchronization produces pending indexes, and the signing job signs and promotes them to the live repository.

To publish the first sample without waiting for scheduled jobs, open **Administration → System → Background Jobs** (`/Jobs`):

1. Run the repository synchronization job (`RepositorySyncJob`) and wait for it to finish successfully.
2. Run the signing/promotion job (`RepositorySignJob`, displayed as **Sign Pending bucket and swap**) and wait for success.
3. Check the repository details and package list for the expected version.

Standalone repositories do not need a mirror synchronization first. If clients download from separate static nodes, also complete export and node synchronization after signing. Check job results rather than relying on a fixed number of minutes.

For the tutorial coordinates, the signed metadata is served at:

```bash title="Check the published repository"
curl --fail --show-error \
  https://apkg.example.com/artifacts/anduinos/dists/resolute-addon/InRelease
```

Receiving this file confirms availability; APT's signature verification in the next step verifies trust.

## Configure a client without Apkg

An end user's machine only needs APT. Open your repository's **Client Configuration Guide** in the web interface and use its actual public key URL, suite, architectures, and source URL. Verify the public key fingerprint against the value supplied by your repository administrator over a trusted channel.

For a certificate named `myrepo` and the tutorial repository, install the key:

```bash title="Download and inspect the repository public key"
curl --fail --show-error --location \
  https://apkg.example.com/artifacts/certs/myrepo \
  --output /tmp/myrepo-apkg.asc
gpg --show-keys --with-fingerprint /tmp/myrepo-apkg.asc
```

After checking the fingerprint:

```bash title="Install the scoped keyring"
gpg --batch --yes --dearmor --output /tmp/myrepo-apkg.gpg /tmp/myrepo-apkg.asc
sudo install -m 0644 /tmp/myrepo-apkg.gpg /usr/share/keyrings/myrepo-apkg.gpg
sudoedit /etc/apt/sources.list.d/myrepo-apkg.sources
```

Put the following in that new `.sources` file:

```text title="/etc/apt/sources.list.d/myrepo-apkg.sources"
Types: deb
URIs: https://apkg.example.com/artifacts/anduinos/
Suites: resolute-addon
Components: main
Architectures: amd64 arm64
Signed-By: /usr/share/keyrings/myrepo-apkg.gpg
```

Use only the architectures you intend to consume; a typical amd64-only machine can use `Architectures: amd64`. `all` describes the sample package's payload, not the client CPU architecture. Preserve the client's existing system repositories, which supply normal dependencies.

```bash title="Verify installation from the repository"
sudo apt update
apt-cache policy hello-apkg
sudo apt install hello-apkg
hello-apkg
dpkg-query -W -f='${Package} ${Version}\n' hello-apkg
```

Verify that `apt-cache policy` lists the intended repository and expected version. On a clean test client, this proves a repository install; a package already installed from a local file is not evidence that the server distributed it. Keep signature verification enabled—do not solve a key error with `trusted=yes`.

### Optional: configure the source with Apkg

If the client already has a system-wide CLI, it can fetch the same settings using the real repository ID:

```bash
sudo apkg add-source --url https://apkg.example.com/api/sources/REPOSITORY_ID
```

This writes a source file and keyring and runs `apt-get update`. Replace `REPOSITORY_ID` before running it. If the CLI is only installed in a user's .NET tools directory, use the manual APT setup above. Do not add the same repository twice through both methods.

## Publish an update

Increase the recipe's version, run the project's tests, and publish the new bundle. After repository sync and signing, the client should see the new candidate:

```bash title="On the client"
sudo apt update
apt-cache policy hello-apkg
sudo apt install --only-upgrade hello-apkg
```

Separate development and production destinations when maintaining a distribution. Do not publish an untested replacement for a core system package to the same source your users consume automatically.

## Automate publication in CI

`apkg deploy --skip-existing` resolves the package targets and asks the destination whether their versions already exist. If the complete target set is present, it skips the build; otherwise it builds and uploads. It is version-based, so changing source without increasing a fixed version can correctly cause the old version to be skipped.

The following GitLab job assumes a Linux runner image with .NET 10, Debian package tools, and every build-time dependency for your project. Create masked/protected `APKG_API_KEY` and an `APKG_SOURCE` variable in CI settings. Set `PACKAGE_DIR` to the recipe directory.

```yaml title="Example GitLab publication job"
publish-package:
  stage: deploy
  variables:
    PACKAGE_DIR: hello-apkg
  before_script:
    - dotnet tool install --tool-path ./.ci-tools Aiursoft.Apkg.Client --version 10.0.55
    - export PATH="$CI_PROJECT_DIR/.ci-tools:$PATH"
  script:
    - cd "$PACKAGE_DIR"
    - apkg --version
    - apkg lint
    - apkg deploy --source "$APKG_SOURCE" --api-key "$APKG_API_KEY" --skip-existing
  rules:
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
```

Include `deploy` in your pipeline's stages. Run application tests in a prior required job or verified prebuild hook. Restrict release credentials to trusted branches and runners. For a recipe at the repository root, use `PACKAGE_DIR: .`.

The [preflight reference](CI-and-Preflight.md) describes fallback behavior for older servers. A successful CI upload still needs the repository publication and APT checks described above.

After an interactive upload session, clear the shell variable:

```bash
unset APKG_API_KEY
```

Next: [Maintenance and troubleshooting](Maintenance-and-Troubleshooting.md).
